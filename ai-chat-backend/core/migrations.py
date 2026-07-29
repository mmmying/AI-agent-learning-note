import logging
import sqlite3
from pathlib import Path

from core.db import Database

logger = logging.getLogger(__name__)

# 迁移 SQL 文件所在目录，文件按名称排序依次执行（因此用数字前缀命名，如 0001_xxx.sql）
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    # 迁移记录表：记录哪些迁移文件已经执行过
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return {name for (name,) in rows}


# 整个流程：建记录表 → 查已执行的 → 按文件名顺序遍历 SQL 文件 → 跳过执行过的、执行新的并记账 → 全部成功则一次性提交事务
def run_migrations(db: Database | None = None) -> list[str]:
    """按顺序执行所有尚未执行过的迁移，返回本次实际执行的迁移文件名列表"""
    db = db or Database()
    executed: list[str] = []
    with db.connect() as conn:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            conn.executescript(sql_file.read_text(encoding="utf-8"))
            # 执行成功后写入迁移记录，下次启动时跳过
            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)",
                (sql_file.name,),
            )
            executed.append(sql_file.name)
            logger.info("已执行数据库迁移: %s", sql_file.name)
    return executed
