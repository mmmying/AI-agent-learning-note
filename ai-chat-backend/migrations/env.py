"""CLI 和应用启动共用的 Alembic 环境。"""

from pathlib import Path

from alembic import context
from core.config import settings
from db.orm_models import Base
from sqlalchemy import create_engine, event, pool
from sqlalchemy.engine import make_url

# Alembic 配置对象
config = context.config

# 告诉 Alembic 当前项目的 ORM 元数据
target_metadata = Base.metadata

database_url = make_url(settings.sqlalchemy_url)
is_sqlite = database_url.get_backend_name() == "sqlite"


def include_object(obj, name, kind, reflected, compared) -> bool:
    # 早期手写迁移的审计表不属于 ORM，保留历史记录。
    return not (kind == "table" and name == "schema_migrations")


def configure_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 批处理重建表是 SQLite 的限制，PostgreSQL 直接执行 DDL。
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 应用和测试传入自己的连接，避免误用默认数据库。
    connection = config.attributes.get("connection")
    if connection is not None:
        configure_connection(connection)
        return

    if is_sqlite and database_url.database:
        Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.sqlalchemy_url, poolclass=pool.NullPool)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys = ON")
            finally:
                cursor.close()

    try:
        with engine.connect() as connection:
            configure_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=is_sqlite,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
