"""通过应用持有的 Engine 执行版本化迁移。"""

from alembic import command
from alembic.config import Config
from core.paths import BASE_DIR
from sqlalchemy.engine import Engine


def migration_config() -> Config:
    return Config(str(BASE_DIR / "alembic.ini"))


def upgrade_database(engine: Engine) -> None:
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
