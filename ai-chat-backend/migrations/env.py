"""CLI 和应用启动共用的 Alembic 环境。"""

from alembic import context
from core.config import settings
from db.orm_models import Base
from sqlalchemy import URL, create_engine, event, pool

# Alembic 配置对象
config = context.config

# 告诉 Alembic 当前项目的 ORM 元数据
target_metadata = Base.metadata


def include_object(obj, name, kind, reflected, compared) -> bool:
    # 早期手写迁移的审计表不属于 ORM，保留历史记录。
    return not (kind == "table" and name == "schema_migrations")


def database_url() -> URL:
    return URL.create("sqlite+pysqlite", database=str(settings.db_path))


def configure_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
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

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(), poolclass=pool.NullPool)

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
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
