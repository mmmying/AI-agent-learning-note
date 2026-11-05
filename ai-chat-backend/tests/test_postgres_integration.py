"""PostgreSQL 方言验证，默认跳过。

启动本地 PostgreSQL（或 docker compose -f ../docker-compose.yml up -d postgres）后，
用一个专用测试库运行——本文件会清空业务表，绝不能指向有真实数据的库：

    docker exec -it <postgres 容器> createdb -U ai_chat ai_chat_test
    TEST_DATABASE_URL="postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat_test" \\
        pytest -m database tests/test_postgres_integration.py
"""

import os

import pytest
from db.database import Database
from db.orm_models import Base, Conversation, MessageRecord
from models.schemas import Message
from services.memory_store import MemoryStore
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url

TEST_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        not TEST_URL.startswith("postgresql"),
        reason="需要 TEST_DATABASE_URL 指向一个 PostgreSQL 测试库",
    ),
]


@pytest.fixture
def pg_database():
    database_name = make_url(TEST_URL).database or ""
    if "test" not in database_name.lower():
        pytest.fail(
            "用例会清空 users、conversations、messages，"
            f"TEST_DATABASE_URL 必须指向专用测试库（当前库名：{database_name}）"
        )
    database = Database(TEST_URL)
    database.init_tables()
    # 用例之间清空业务表，让同一个库可以反复运行。
    with database.engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield database
    database.engine.dispose()


def test_migrations_record_head_and_drop_rate_limit_table(pg_database):
    with pg_database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        )
    tables = set(inspect(pg_database.engine).get_table_names())
    assert {"users", "conversations", "messages"} <= tables
    # 限流计数改由 Redis 保存。
    assert "auth_rate_limits" not in tables


def test_messages_id_is_database_generated(pg_database):
    columns = {
        column["name"]: column
        for column in inspect(pg_database.engine).get_columns("messages")
    }
    # 迁移必须给自增主键配好序列，否则 ORM 只传业务列会撞 NOT NULL。
    assert "nextval" in (columns["id"]["default"] or "")


def test_conversation_foreign_key_is_enforced(pg_database):
    with pytest.raises(Exception), pg_database.session() as session:
        session.add(
            MessageRecord(conversation_id="missing", role="user", content="孤立消息")
        )
        session.commit()


def test_conversation_user_foreign_key_is_declared(pg_database):
    # 会话归属用户的外键在 PostgreSQL 上同样生效，保证多用户隔离。
    foreign_keys = inspect(pg_database.engine).get_foreign_keys("conversations")
    assert any(fk["referred_table"] == "users" for fk in foreign_keys)


def test_memory_store_round_trip(pg_database):
    with pg_database.session() as session:
        store = MemoryStore(session, user_id=None)
        store.add_message("pg-conversation", Message(role="user", content="第一句"))
        store.add_message(
            "pg-conversation", Message(role="assistant", content="第二句")
        )
        session.commit()

    with pg_database.session() as session:
        ids = [
            row.id
            for row in session.scalars(select(MessageRecord).order_by(MessageRecord.id))
        ]
        assert len(ids) == 2 and ids[1] > ids[0]
        # timestamptz 读回必须带时区，否则迁移后的时间比较会失真。
        assert session.scalar(select(MessageRecord.created_at)).tzinfo is not None
        assert MemoryStore(session).conversation_exists("pg-conversation")
        # 标题只在会话创建时取首条消息，后续消息不覆盖。
        assert (
            session.scalar(
                select(Conversation.title).where(Conversation.id == "pg-conversation")
            )
            == "第一句"
        )

    with pg_database.session() as session:
        assert MemoryStore(session).delete_conversation("pg-conversation")
        session.commit()
    with pg_database.session() as session:
        # 级联删除由数据库执行，消息随会话一并清除。
        assert session.scalar(select(func.count()).select_from(MessageRecord)) == 0
