import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from db.orm_models import Conversation, MessageRecord
from models.schemas import Message
from services.memory_store import MemoryStore

pytestmark = pytest.mark.database


def test_foreign_keys_enabled_on_every_connection(database):
    # 同时占用两条连接，确保不是重复检查同一条池连接。
    with database.engine.connect() as first, database.engine.connect() as second:
        assert first.scalar(text("PRAGMA foreign_keys")) == 1
        assert second.scalar(text("PRAGMA foreign_keys")) == 1


@pytest.mark.parametrize("field,value", [
    ("conversation_id", "missing"), ("conversation_id", None),
    ("role", None), ("content", None),
])
def test_message_constraints_rollback_entire_transaction(database, field, value):
    with pytest.raises(IntegrityError):
        with database.session() as session:
            session.add(Conversation(id="parent", title="标题"))
            session.flush()
            values = {"conversation_id": "parent", "role": "user", "content": "hello"}
            values[field] = value
            session.add(MessageRecord(**values))
            session.commit()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(MessageRecord)) == 0
        # 失败后仍可通过新 Session 完成正常事务。
        MemoryStore(session).add_message("recovered", Message(role="user", content="ok"))
        session.commit()
    with database.session() as session:
        assert MemoryStore(session).conversation_exists("recovered")


def test_database_cascade_only_removes_target_conversation(database):
    with database.session() as session:
        store = MemoryStore(session)
        for conversation_id in ["deleted", "kept"]:
            store.add_message(conversation_id, Message(role="user", content=conversation_id))
        session.commit()
    # 直接 SQL 删除父表，验证级联由数据库执行，而非 ORM relationship。
    with database.engine.begin() as connection:
        connection.execute(delete(Conversation).where(Conversation.id == "deleted"))
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MessageRecord)) == 1
        assert MemoryStore(session).get_messages("kept") == [Message(role="user", content="kept")]


def test_session_without_commit_discards_pending_deletion(database):
    with database.session() as session:
        MemoryStore(session).add_message("kept", Message(role="user", content="hello"))
        session.commit()
    with database.session() as session:
        assert MemoryStore(session).delete_conversation("kept")
    with database.session() as session:
        assert MemoryStore(session).conversation_exists("kept")
        assert len(MemoryStore(session).get_messages("kept")) == 1
