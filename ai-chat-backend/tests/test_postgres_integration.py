"""PostgreSQL 方言验证，默认跳过。

启动本地 PostgreSQL（或 docker compose -f ../docker-compose.yml up -d postgres）后，
用一个专用测试库运行——本文件会清空业务表，绝不能指向有真实数据的库：

    docker exec -it <postgres 容器> createdb -U ai_chat ai_chat_test
    TEST_DATABASE_URL="postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat_test" \\
        pytest -m database tests/test_postgres_integration.py
"""

import os

import pytest
from alembic import command
from db.database import Database
from db.migrations import migration_config
from db.orm_models import Base, Conversation, MessageRecord, User
from fastapi import HTTPException
from models.schemas import Message, TokenUsage
from services.memory_store import MemoryStore
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

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
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
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


def test_token_usage_accumulates(pg_database):
    with pg_database.session() as session:
        store = MemoryStore(session, user_id=None)
        store.add_message("tokens", Message(role="user", content="第一句"))
        store.add_token_usage(
            "tokens", TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
        )
        store.add_token_usage(
            "tokens", TokenUsage(input_tokens=20, output_tokens=7, total_tokens=27)
        )
        session.commit()

    with pg_database.session() as session:
        summary = MemoryStore(session).list_conversations()[0]
        assert summary.usage.input_tokens == 30
        assert summary.usage.output_tokens == 12
        assert summary.usage.total_tokens == 42


def test_context_summary_persists_and_keeps_full_history(pg_database):
    messages = [
        Message(role="user", content="第一问"),
        Message(role="assistant", content="第一答"),
        Message(role="user", content="第二问"),
        Message(role="assistant", content="第二答"),
    ]
    with pg_database.session() as session:
        store = MemoryStore(session)
        for message in messages:
            store.add_message("context", message)
            store.add_message("other", Message(role="user", content="其他会话"))
        summary, records = store.get_context("context")
        assert summary == ""
        assert all(isinstance(row, MessageRecord) for row in records)
        assert [row.content for row in records] == [m.content for m in messages]
        ids = [row.id for row in records]
        assert ids == sorted(ids)
        assert session.get(Conversation, "context").summary_through_id == 0
        store.save_context_summary("context", "第一轮摘要", ids[1])
        session.commit()

    with pg_database.session() as session:
        store = MemoryStore(session)
        summary, records = store.get_context("context")
        assert summary == "第一轮摘要"
        assert [row.id for row in records] == ids[2:]
        assert store.get_messages("context") == messages
        assert session.get(Conversation, "context").summary_through_id == ids[1]
        store.add_message("context", Message(role="user", content="未提交消息"))
        store.save_context_summary("context", "未提交摘要", ids[-1])
        # 不提交，消息和摘要更新必须一起回滚。

    with pg_database.session() as session:
        store = MemoryStore(session)
        summary, records = store.get_context("context")
        assert summary == "第一轮摘要"
        assert [row.id for row in records] == ids[2:]
        assert store.get_messages("context") == messages
        store.save_context_summary("context", "全部已摘要", ids[-1])
        assert store.get_context("context") == ("全部已摘要", [])
        assert store.get_messages("context") == messages


def test_context_summary_enforces_user_isolation(pg_database):
    with pg_database.session() as session:
        session.add_all([
            User(id="owner", username="owner", password_hash="test-only"),
            User(id="other", username="other", password_hash="test-only"),
        ])
        session.flush()
        for conversation_id, user_id in [("private", "owner"), ("legacy", None)]:
            store = MemoryStore(session, user_id)
            store.add_message(conversation_id, Message(role="user", content="私有历史"))
            _, records = store.get_context(conversation_id)
            store.save_context_summary(conversation_id, "私有摘要", records[0].id)
        session.commit()

    with pg_database.session() as session:
        store = MemoryStore(session, "other")
        for conversation_id in ["private", "legacy", "missing"]:
            with pytest.raises(HTTPException) as error:
                store.get_context(conversation_id)
            assert error.value.status_code == 404
            store.save_context_summary(conversation_id, "越权摘要", 999999)
            assert store.get_messages(conversation_id) == []
        for conversation_id in ["private", "legacy"]:
            with pytest.raises(HTTPException) as error:
                store.add_message(conversation_id, Message(role="user", content="越权消息"))
            assert error.value.status_code == 404
        session.commit()

    with pg_database.session() as session:
        for conversation_id, user_id in [("private", "owner"), ("legacy", None)]:
            store = MemoryStore(session, user_id)
            assert store.get_context(conversation_id) == ("私有摘要", [])
            assert store.get_messages(conversation_id) == [Message(role="user", content="私有历史")]
            row = session.get(Conversation, conversation_id)
            assert row.user_id == user_id
            assert row.summary_through_id == session.scalar(
                select(MessageRecord.id).where(MessageRecord.conversation_id == conversation_id)
            )


def test_add_message_locks_conversation_before_inserting(pg_database):
    with pg_database.session() as session:
        MemoryStore(session).add_message("locked", Message(role="user", content="原消息"))
        session.commit()

    with pg_database.session() as holder:
        holder.scalar(select(Conversation.id).where(Conversation.id == "locked").with_for_update())
        with pg_database.session() as contender:
            contender.execute(text("SET LOCAL lock_timeout = '250ms'"))
            contender.execute(text("SET LOCAL statement_timeout = '5s'"))
            with pytest.raises(OperationalError) as error:
                MemoryStore(contender).add_message("locked", Message(role="user", content="等待锁"))
            assert error.value.orig.sqlstate == "55P03"
            # 若到 INSERT 或更新时间才阻塞，消息 ID 可能已乱序；必须先锁会话。
            assert "FOR UPDATE" in error.value.statement
            contender.rollback()
        holder.commit()

    with pg_database.session() as session:
        store = MemoryStore(session)
        assert [m.content for m in store.get_messages("locked")] == ["原消息"]
        store.add_message("locked", Message(role="assistant", content="锁释放后可写"))
        _, records = store.get_context("locked")
        store.save_context_summary("locked", "已完成", records[-1].id)
        session.commit()
    with pg_database.session() as session:
        assert MemoryStore(session).get_context("locked") == ("已完成", [])


@pytest.mark.parametrize("stream", [False, True])
def test_chat_compaction_on_postgres(pg_database, ai_client, monkeypatch, stream):
    from core.config import settings
    from models.schemas import ChatRequest
    from services.chat_service import ChatService

    monkeypatch.setattr(settings, "context_max_tokens", 8192)
    monkeypatch.setattr(settings, "context_response_reserve", 2048)
    monkeypatch.setattr(settings, "context_summary_max_tokens", 512)
    monkeypatch.setattr(settings, "context_recent_turns", 1)
    with pg_database.session() as session:
        store = MemoryStore(session)
        for _ in range(3):
            store.add_message("long", Message(role="user", content="a" * 1000))
            store.add_message("long", Message(role="assistant", content="b" * 1500))
        session.commit()

    with pg_database.session() as session:
        service = ChatService(MemoryStore(session))
        request = ChatRequest(conversation_id="long", message="继续")
        if stream:
            events = list(service.chat_stream(request))
            assert '"type": "done"' in events[-1]
        else:
            assert service.chat(request).reply == "测试回复"

    with pg_database.session() as session:
        store = MemoryStore(session)
        assert len(store.get_messages("long")) == 8
        summary, recent = store.get_context("long")
        assert summary == "测试回复"
        assert len(recent) == 4
        assert store.list_conversations()[0].usage.total_tokens == (
            ai_client.chat.call_count + int(stream)
        ) * 15


def test_context_summary_migration_defaults_for_existing_data(pg_database):
    config = migration_config()
    try:
        with pg_database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0006")
            columns = {c["name"] for c in inspect(connection).get_columns("conversations")}
            assert "context_summary" not in columns
            assert "summary_through_id" not in columns
            connection.execute(text(
                "INSERT INTO conversations (id, title, created_at, updated_at) "
                "VALUES ('old', '旧会话', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            connection.execute(text(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES ('old', 'user', '完整历史', CURRENT_TIMESTAMP)"
            ))
        pg_database.init_tables()
        with pg_database.engine.begin() as connection:
            assert tuple(connection.execute(text(
                "SELECT context_summary, summary_through_id FROM conversations WHERE id = 'old'"
            )).one()) == ("", 0)
            assert connection.scalar(text("SELECT content FROM messages")) == "完整历史"
            connection.execute(text(
                "INSERT INTO conversations (id, title, created_at, updated_at) "
                "VALUES ('new', '新会话', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            assert tuple(connection.execute(text(
                "SELECT context_summary, summary_through_id FROM conversations WHERE id = 'new'"
            )).one()) == ("", 0)
            columns = {c["name"]: c for c in inspect(connection).get_columns("conversations")}
            assert not columns["context_summary"]["nullable"]
            assert not columns["summary_through_id"]["nullable"]
            assert not any(
                "summary_through_id" in fk["constrained_columns"]
                for fk in inspect(connection).get_foreign_keys("conversations")
            )
    finally:
        # 即使断言失败，也恢复专用测试库的迁移版本，避免干扰后续用例。
        with pg_database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
