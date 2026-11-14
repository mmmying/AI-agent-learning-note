import tempfile
import unittest
import pytest
from datetime import datetime
from pathlib import Path

from db.database import Database, init_db
from db.orm_models import Conversation, MessageRecord, User
from fastapi import HTTPException
from models.schemas import Message
from services.memory_store import MemoryStore
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.database


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "chat.db")
        init_db(self.db)
        self.session_context = self.db.session()
        self.session = self.session_context.__enter__()
        self.store = MemoryStore(self.session)

    def tearDown(self):
        self.session_context.__exit__(None, None, None)
        self.db.engine.dispose()
        self.temp.cleanup()

    def test_initialization_preserves_existing_data(self):
        fresh_db = Database(Path(self.temp.name) / "orm_only.db")
        try:
            init_db(fresh_db)
            with fresh_db.session() as session:
                MemoryStore(session).add_message("orm", Message(role="user", content="ORM 建表"))
                session.commit()
            init_db(fresh_db)
            init_db(fresh_db)
            with fresh_db.session() as session:
                store = MemoryStore(session)
                self.assertEqual(
                    store.get_messages("orm"), [Message(role="user", content="ORM 建表")]
                )
                self.assertTrue(store.delete_conversation("orm"))
                self.assertEqual(store.get_messages("orm"), [])
        finally:
            fresh_db.engine.dispose()

    def test_messages_title_and_cascade(self):
        self.assertFalse(self.store.conversation_exists("chat"))
        self.assertEqual(self.store.get_messages("chat"), [])
        messages = [
            Message(role="user", content="你" * 60),
            Message(role="assistant", content="回复"),
        ]
        for message in messages:
            self.store.add_message("chat", message)
        self.assertTrue(self.store.conversation_exists("chat"))
        self.assertEqual(self.store.get_messages("chat"), messages)
        summary = self.store.list_conversations()[0]
        self.assertEqual(summary.title, "你" * 50)
        self.assertIsInstance(summary.created_at, datetime)
        self.assertIsInstance(summary.updated_at, datetime)
        self.assertEqual(
            summary.model_dump(mode="json")["created_at"],
            summary.created_at.isoformat(),
        )
        self.assertTrue(self.store.delete_conversation("chat"))
        self.assertFalse(self.store.delete_conversation("chat"))
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MessageRecord)), 0
        )

    def test_existing_data_and_sort_order(self):
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("old", "原有标题", "2020-01-01 00:00:00", "2020-01-01 00:00:00"),
            )
            conn.exec_driver_sql(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                ("old", "user", "历史消息", "2020-01-01 00:00:00"),
            )
        self.store.add_message("new", Message(role="user", content="新消息"))
        self.assertEqual(
            [c.id for c in self.store.list_conversations()], ["new", "old"]
        )
        self.store.add_message("old", Message(role="assistant", content="继续"))
        self.assertEqual(
            [m.content for m in self.store.get_messages("old")], ["历史消息", "继续"]
        )
        old = next(c for c in self.store.list_conversations() if c.id == "old")
        self.assertEqual(old.title, "原有标题")
        self.assertEqual(old.created_at, datetime(2020, 1, 1))
        self.assertGreater(old.updated_at, old.created_at)

    def test_failed_message_rolls_back_conversation(self):
        invalid = Message.model_construct(role=None, content="失败")
        with self.assertRaises(IntegrityError):
            with self.db.session() as session:
                MemoryStore(session).add_message("failed", invalid)
        self.assertFalse(self.store.conversation_exists("failed"))
        with self.assertRaises(IntegrityError):
            with self.db.session() as session:
                session.add(
                    MessageRecord(conversation_id="missing", role="user", content="x")
                )
                session.flush()

    def test_session_exit_does_not_commit(self):
        with self.db.session() as session:
            MemoryStore(session).add_message("uncommitted", Message(role="user", content="x"))
        self.assertFalse(self.store.conversation_exists("uncommitted"))

    def test_explicit_commit_persists(self):
        with self.db.session() as session:
            MemoryStore(session).add_message("committed", Message(role="user", content="x"))
            session.commit()
        self.assertTrue(self.store.conversation_exists("committed"))

    def test_session_can_commit_multiple_transactions(self):
        with self.db.session() as session:
            store = MemoryStore(session)
            store.add_message("multiple", Message(role="user", content="one"))
            session.commit()
            store.add_message("multiple", Message(role="assistant", content="two"))
            session.commit()
        self.assertEqual(
            [m.content for m in self.store.get_messages("multiple")], ["one", "two"]
        )

    def test_context_summary_persists_without_truncating_history(self):
        messages = [
            Message(role="user", content="第一问"),
            Message(role="assistant", content="第一答"),
            Message(role="user", content="第二问"),
            Message(role="assistant", content="第二答"),
        ]
        with self.db.session() as session:
            store = MemoryStore(session)
            for message in messages:
                store.add_message("context", message)
                store.add_message("other", Message(role="user", content="其他会话"))
            summary, records = store.get_context("context")
            self.assertEqual(summary, "")
            self.assertTrue(all(isinstance(row, MessageRecord) for row in records))
            self.assertEqual([row.content for row in records], [m.content for m in messages])
            ids = [row.id for row in records]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(session.get(Conversation, "context").summary_through_id, 0)
            store.save_context_summary("context", "第一轮的摘要", ids[1])
            session.commit()

        with self.db.session() as session:
            store = MemoryStore(session)
            summary, records = store.get_context("context")
            self.assertEqual(summary, "第一轮的摘要")
            self.assertEqual([row.id for row in records], ids[2:])
            self.assertEqual(store.get_messages("context"), messages)
            self.assertEqual(session.get(Conversation, "context").summary_through_id, ids[1])
            store.save_context_summary("context", "全部已摘要", ids[-1])
            self.assertEqual(store.get_context("context"), ("全部已摘要", []))
            self.assertEqual(store.get_messages("context"), messages)

    def test_summary_and_messages_roll_back_together_without_commit(self):
        with self.db.session() as session:
            store = MemoryStore(session)
            store.add_message("rollback-context", Message(role="user", content="第一句"))
            store.add_message("rollback-context", Message(role="assistant", content="第二句"))
            _, records = store.get_context("rollback-context")
            ids = [row.id for row in records]
            store.save_context_summary("rollback-context", "原摘要", ids[0])
            session.commit()

        with self.db.session() as session:
            store = MemoryStore(session)
            store.add_message("rollback-context", Message(role="user", content="未提交消息"))
            store.save_context_summary("rollback-context", "未提交摘要", ids[1])

        with self.db.session() as session:
            store = MemoryStore(session)
            summary, records = store.get_context("rollback-context")
            self.assertEqual(summary, "原摘要")
            self.assertEqual([row.id for row in records], ids[1:])
            self.assertEqual(
                [m.content for m in store.get_messages("rollback-context")],
                ["第一句", "第二句"],
            )

    def test_context_operations_enforce_user_isolation(self):
        with self.db.session() as session:
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

        with self.db.session() as session:
            store = MemoryStore(session, "other")
            for conversation_id in ["private", "legacy", "missing"]:
                with self.assertRaises(HTTPException) as error:
                    store.get_context(conversation_id)
                self.assertEqual(error.exception.status_code, 404)
                store.save_context_summary(conversation_id, "越权摘要", 999999)
                self.assertEqual(store.get_messages(conversation_id), [])
            for conversation_id in ["private", "legacy"]:
                with self.assertRaises(HTTPException) as error:
                    store.add_message(conversation_id, Message(role="user", content="越权消息"))
                self.assertEqual(error.exception.status_code, 404)
            store.add_message("own", Message(role="user", content="自己的消息"))
            self.assertEqual(
                [row.content for row in store.get_context("own")[1]], ["自己的消息"]
            )
            session.commit()

        with self.db.session() as session:
            for conversation_id, user_id in [("private", "owner"), ("legacy", None)]:
                store = MemoryStore(session, user_id)
                self.assertEqual(store.get_context(conversation_id), ("私有摘要", []))
                self.assertEqual(
                    store.get_messages(conversation_id),
                    [Message(role="user", content="私有历史")],
                )
                row = session.get(Conversation, conversation_id)
                self.assertEqual(row.user_id, user_id)
                self.assertEqual(
                    row.summary_through_id,
                    session.scalar(select(MessageRecord.id).where(
                        MessageRecord.conversation_id == conversation_id
                    )),
                )

    def test_context_python_defaults(self):
        conversation = Conversation(id="orm-defaults", title="ORM 默认值")
        self.session.add(conversation)
        self.session.flush()
        self.assertEqual(conversation.context_summary, "")
        self.assertEqual(conversation.summary_through_id, 0)
        self.assertEqual(self.store.get_context("orm-defaults"), ("", []))


if __name__ == "__main__":
    unittest.main()
