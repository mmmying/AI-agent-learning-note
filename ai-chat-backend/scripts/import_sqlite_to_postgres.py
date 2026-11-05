"""一次性把本地 SQLite 运行库导入 PostgreSQL。

用法（先停服务并备份 SQLite 文件）：

    python scripts/import_sqlite_to_postgres.py \\
        --sqlite-path data/chat.db \\
        --database-url "postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat"

目标库会先升级到最新迁移再导入；脚本把整张表读进内存逐行校验，
适合个人聊天库这种规模，不适合需要长时间在线迁移的大表。
auth_rate_limits 不迁移：限流计数改由 Redis 保存，切换后从 0 重新累计。
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# 脚本从 scripts/ 启动时，项目根目录不在 sys.path 中，需要显式加入才能导入应用模块。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from db.database import Database, to_url
from db.orm_models import Conversation, MessageRecord, User
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import Table

# 时间列的来源不同：users.created_at 由 Python 的 utc_now() 写入，
# 会话与消息时间由 SQL 的 datetime('now', 'localtime') 写入。
# 两处都是去掉时区的朴素字符串，所以必须按列选择解释基准。
TABLES: tuple[tuple[Table, frozenset[str]], ...] = (
    (User.__table__, frozenset({"created_at"})),
    (Conversation.__table__, frozenset()),
    (MessageRecord.__table__, frozenset()),
)


def to_utc(value: datetime, assume_utc: bool) -> datetime:
    """把朴素时间戳统一成带 UTC 时区的值。"""
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    if assume_utc:
        return value.replace(tzinfo=UTC)
    # 朴素时间按运行脚本这台机器的本地时区解释，与 datetime('now','localtime') 一致。
    return value.astimezone(UTC)


def read_rows(source: Engine, table: Table, utc_columns: frozenset[str]) -> list[dict]:
    """按主键顺序读出全表，并把时间列规范成 UTC。"""
    with source.connect() as connection:
        rows = [dict(row._mapping) for row in connection.execute(select(table))]
    primary_keys = [column.name for column in table.primary_key]
    rows.sort(key=lambda row: tuple(row[name] for name in primary_keys))
    for row in rows:
        for column in table.columns:
            if isinstance(row[column.name], datetime):
                row[column.name] = to_utc(row[column.name], column.name in utc_columns)
    return rows


def assert_empty_target(target: Engine, force: bool) -> None:
    with target.connect() as connection:
        counts = {
            table.name: connection.scalar(select(func.count()).select_from(table))
            for table, _ in TABLES
        }
    if not any(counts.values()):
        return
    detail = "、".join(f"{name} {count} 行" for name, count in counts.items())
    if force:
        print(f"目标库已有数据（{detail}），按 --force 继续追加。")
        return
    raise SystemExit(
        f"目标库不是空库（{detail}）。确认要合并请加 --force，否则先清空或换库。"
    )


def reset_message_sequence(target: Engine) -> None:
    """显式主键导入不会推进序列，这里把 messages.id 对齐到最大值。"""
    with target.begin() as connection:
        connection.execute(
            text(
                "SELECT setval("
                "  pg_get_serial_sequence('messages', 'id'),"
                "  (SELECT COALESCE(max(id), 1) FROM messages),"
                "  true)"
            )
        )


def verify(target: Engine, expected: dict[str, list[dict]]) -> None:
    """逐行比对导入结果，而不只是数行数。"""
    with target.connect() as connection:
        for table_name, rows in expected.items():
            table = next(t for t, _ in TABLES if t.name == table_name)
            actual = [
                dict(row._mapping)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key)
                )
            ]
            if actual != rows:
                raise SystemExit(
                    f"{table_name} 校验失败：源 {len(rows)} 行，目标 {len(actual)} 行，内容不一致。"
                )
            print(f"  {table_name}: {len(rows)} 行一致")


def import_database(
    sqlite_path: Path, database_url: str, force: bool
) -> dict[str, list[dict]]:
    source = create_engine(to_url(sqlite_path))
    database = Database(database_url)
    try:
        # 导入前先让目标库结构升级到 head。
        database.init_tables()
        target = database.engine
        assert_empty_target(target, force)
        rows_by_table = {
            table.name: read_rows(source, table, utc_columns)
            for table, utc_columns in TABLES
        }
        for table_name, rows in rows_by_table.items():
            print(f"{table_name}: 读取 {len(rows)} 行")
        try:
            with target.begin() as connection:
                for table, _ in TABLES:
                    rows = rows_by_table[table.name]
                    if rows:
                        connection.execute(table.insert(), rows)
                    print(f"{table.name}: 写入 {len(rows)} 行")
        except IntegrityError as error:
            raise SystemExit(
                f"导入中断，事务已回滚，目标库未留下部分数据：{error}"
            ) from error
        reset_message_sequence(target)
        print("逐行校验：")
        verify(target, rows_by_table)
        return rows_by_table
    finally:
        source.dispose()
        database.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 SQLite 聊天库导入 PostgreSQL")
    parser.add_argument("--sqlite-path", type=Path, default=settings.db_path)
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument(
        "--force", action="store_true", help="目标库非空时仍继续追加导入"
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        raise SystemExit(
            "请用 --database-url 或环境变量 DATABASE_URL 指定 PostgreSQL 连接串"
        )
    if make_url(args.database_url).get_backend_name() != "postgresql":
        raise SystemExit(
            "--database-url 必须是 PostgreSQL 连接串（postgresql+psycopg://...）"
        )
    if not args.sqlite_path.exists():
        raise SystemExit(f"找不到源数据库文件：{args.sqlite_path}")

    # 只读源库，导入过程不改动 SQLite，方便反复重试。
    rows_by_table = import_database(args.sqlite_path, args.database_url, args.force)

    # 打印时间范围，便于人工确认时区换算没有整体偏移一个小时。
    for table_name, rows in rows_by_table.items():
        stamps = [
            value
            for row in rows
            for value in row.values()
            if isinstance(value, datetime)
        ]
        if stamps:
            print(f"{table_name} 时间范围（UTC）：{min(stamps)} ~ {max(stamps)}")
    print("导入完成。限流计数不迁移，Redis 从 0 开始累计。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
