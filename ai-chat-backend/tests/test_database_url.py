"""连接串解析与方言差异的单元测试。"""

from pathlib import Path

import pytest
from core.config import Settings
from db.database import to_url
from db.dialect import insert_statement, now_expression
from db.orm_models import Conversation
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.database


def settings(**values):
    return Settings(_env_file=None, **values)


def test_sqlalchemy_url_falls_back_to_sqlite_file():
    value = settings(db_path="data/chat.db").sqlalchemy_url
    assert make_url(value).get_backend_name() == "sqlite"
    # Settings 把 db_path 转成 Path，Windows 上分隔符会变成反斜杠。
    assert Path(make_url(value).database) == Path("data/chat.db")


def test_sqlalchemy_url_prefers_configured_database_url():
    url = "postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat"
    value = settings(database_url=url).sqlalchemy_url
    assert value == url
    assert make_url(value).get_backend_name() == "postgresql"


def test_production_requires_postgresql_database_url():
    secret = "x" * 40
    with pytest.raises(ValueError, match="PostgreSQL"):
        settings(
            app_env="production",
            jwt_secret_key=secret,
            database_url="sqlite:///data/chat.db",
        )
    value = settings(
        app_env="production",
        jwt_secret_key=secret,
        database_url="postgresql+psycopg://ai_chat:ai_chat@db:5432/ai_chat",
    )
    assert value.app_env == "production"


def test_production_still_requires_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        settings(
            app_env="production",
            database_url="postgresql+psycopg://ai_chat:ai_chat@db:5432/ai_chat",
        )


@pytest.mark.parametrize("value", ["data/chat.db", "E:\\data\\chat.db"])
def test_to_url_wraps_bare_sqlite_path(value):
    url = to_url(value)
    assert url.startswith("sqlite+pysqlite:///")
    assert make_url(url).database.endswith("chat.db")


def test_to_url_keeps_full_database_url():
    url = "postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat"
    assert to_url(url) == url


def test_now_expression_per_dialect():
    # SQLite 把参数渲染成占位符，展开字面量后才能看到实际的时间表达式。
    assert (
        str(
            now_expression("sqlite").compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        == "datetime('now', 'localtime')"
    )
    assert str(now_expression("postgresql").compile(dialect=postgresql.dialect())) == (
        "now()"
    )


def test_insert_statement_supports_on_conflict_for_both_dialects():
    for name, dialect in (
        ("sqlite", sqlite.dialect()),
        ("postgresql", postgresql.dialect()),
    ):
        statement = (
            insert_statement(name)(Conversation)
            .values(id="new")
            .on_conflict_do_nothing(index_elements=[Conversation.id])
        )
        assert "ON CONFLICT (id) DO NOTHING" in str(statement.compile(dialect=dialect))


def test_dialect_helpers_reject_unknown_backend():
    with pytest.raises(ValueError, match="不支持的数据库方言"):
        now_expression("mysql")
    with pytest.raises(ValueError, match="不支持的数据库方言"):
        insert_statement("mysql")
