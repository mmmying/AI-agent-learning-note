import tempfile
import unittest
import pytest
from pathlib import Path
from unittest.mock import patch

from api.chat import router as chat_router
from api.conversations import router as conversations_router
from db.database import Database
from db.dependencies import get_database
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.memory_store import MemoryStore
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.database, pytest.mark.api]


class SessionDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.db")
        self.database.init_tables()
        app = FastAPI()
        app.include_router(chat_router)
        app.include_router(conversations_router)
        # 当 FastAPI 需要调用 get_database() 时，改为调用这个 lambda，返回测试数据库
        app.dependency_overrides[get_database] = lambda: self.database
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.database.engine.dispose()
        self.temp.cleanup()

    def test_chat_persists_and_delete_commits(self):
        with patch("services.chat_service.AIClient") as client:
            client.return_value.chat.return_value = "reply"
            response = self.client.post("/chat/chat", json={"message": "hello"})
        self.assertEqual(response.status_code, 200)
        conversation_id = response.json()["conversation_id"]
        messages = self.client.get(f"/conversations/{conversation_id}/messages")
        self.assertEqual([m["content"] for m in messages.json()], ["hello", "reply"])
        self.assertEqual(
            self.client.delete(f"/conversations/{conversation_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/conversations/{conversation_id}/messages").status_code,
            404,
        )

    def test_chat_failure_rolls_back(self):
        with patch("services.chat_service.AIClient") as client:
            client.return_value.chat.side_effect = RuntimeError("failed")
            with self.assertRaises(RuntimeError):
                self.client.post("/chat/chat", json={"message": "hello"})
        with self.database.session() as session:
            self.assertEqual(MemoryStore(session).list_conversations(), [])

    def test_stream_keeps_session_until_completion(self):
        with patch("services.chat_service.AIClient") as client:
            client.return_value.chat_stream.return_value = iter(["one", "two"])
            response = self.client.post("/chat/stream", json={"message": "hello"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "done"', response.text)
        with self.database.session() as session:
            store = MemoryStore(session)
            conversation = store.list_conversations()[0]
            self.assertEqual(
                [m.content for m in store.get_messages(conversation.id)],
                ["hello", "onetwo"],
            )

    def test_commit_failure_rolls_back(self):
        with patch("services.chat_service.AIClient") as client:
            client.return_value.chat.return_value = "reply"
            with (
                patch.object(
                    Session, "commit", side_effect=RuntimeError("commit failed")
                ),
                self.assertRaisesRegex(RuntimeError, "commit failed"),
            ):
                self.client.post("/chat/chat", json={"message": "hello"})
        with self.database.session() as session:
            self.assertEqual(MemoryStore(session).list_conversations(), [])
