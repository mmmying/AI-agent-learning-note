import json

import httpx
import pytest
from openai import APIError, AuthenticationError, RateLimitError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import MessageRecord
from services.memory_store import MemoryStore

pytestmark = [pytest.mark.api, pytest.mark.database]


def test_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["env"] == "test"


@pytest.mark.parametrize("provider", [None, "openai", "deepseek"])
def test_chat_persists_full_round(client, database, ai_client, provider):
    response = client.post("/chat/chat", json={"message": "你好", "provider": provider})
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]
    assert response.json()["reply"] == "测试回复"
    with database.session() as session:
        store = MemoryStore(session, client.user_id)
        assert [m.content for m in store.get_messages(conversation_id)] == ["你好", "测试回复"]
        assert store.list_conversations()[0].title == "你好"
    ai_client.chat.assert_called_once()


def test_chat_passes_history_to_ai(client, ai_client):
    first = client.post("/chat/chat", json={"message": "第一问"}).json()
    response = client.post("/chat/chat", json={
        "message": "第二问", "conversation_id": first["conversation_id"],
    })
    assert response.status_code == 200
    assert response.json()["conversation_id"] == first["conversation_id"]
    assert [m.content for m in ai_client.chat.call_args.kwargs["messages"]] == [
        "第一问", "测试回复", "第二问",
    ]
    assert len(client.get("/conversations").json()) == 1


@pytest.mark.parametrize("endpoint", ["/chat/chat", "/chat/stream"])
@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": None},
                                        {"message": "hello", "provider": "invalid"}])
def test_invalid_chat_does_not_write(client, database, ai_client, endpoint, payload):
    assert client.post(endpoint, json=payload).status_code == 422
    ai_client.chat.assert_not_called()
    ai_client.chat_stream.assert_not_called()
    with database.session() as session:
        assert MemoryStore(session, client.user_id).list_conversations() == []


def test_conversation_list_messages_and_delete(client, database):
    assert client.get("/conversations").json() == []
    conversation_id = client.post("/chat/chat", json={"message": "你好"}).json()["conversation_id"]
    listing = client.get("/conversations")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == conversation_id
    messages = client.get(f"/conversations/{conversation_id}/messages")
    assert messages.status_code == 200
    assert messages.json() == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "测试回复"},
    ]
    deleted = client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/conversations").json() == []
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MessageRecord)) == 0


@pytest.mark.parametrize("method,suffix", [("GET", "/messages"), ("DELETE", "")])
def test_missing_conversation(client, method, suffix):
    response = client.request(method, f"/conversations/missing{suffix}")
    assert response.status_code == 404
    assert response.json() == {"detail": "会话不存在"}


def test_stream_events_and_persistence(client, database):
    response = client.post("/chat/stream", json={"message": "你好"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = [json.loads(line.removeprefix("data: "))
              for line in response.text.splitlines() if line.startswith("data: ")]
    conversation_id = events[0]["conversation_id"]
    assert events == [
        {"type": "start", "conversation_id": conversation_id},
        {"type": "delta", "content": "测试"},
        {"type": "delta", "content": "回复"},
        {"type": "done", "conversation_id": conversation_id},
    ]
    with database.session() as session:
        assert [m.content for m in MemoryStore(session, client.user_id).get_messages(conversation_id)] == ["你好", "测试回复"]


@pytest.mark.parametrize("error_type,status", [
    (AuthenticationError, 502), (RateLimitError, 429), (APIError, 502),
])
def test_ai_errors_are_mapped_and_rolled_back(client, database, ai_client, error_type, status):
    request = httpx.Request("POST", "https://ai.invalid")
    if error_type is APIError:
        error = error_type("upstream failed", request=request, body=None)
    else:
        error = error_type("upstream failed", response=httpx.Response(status, request=request), body=None)
    ai_client.chat.side_effect = error
    response = client.post("/chat/chat", json={"message": "失败"})
    assert response.status_code == status
    assert response.json()["detail"]
    with database.session() as session:
        assert MemoryStore(session, client.user_id).list_conversations() == []


def test_stream_failure_preserves_previous_round(client, database, ai_client):
    conversation_id = client.post("/chat/chat", json={"message": "已保存"}).json()["conversation_id"]

    def failing_stream(**kwargs):
        yield "部分回复"
        raise RuntimeError("stream failed")

    ai_client.chat_stream.side_effect = failing_stream
    with pytest.raises(RuntimeError, match="stream failed"):
        client.post("/chat/stream", json={"message": "失败", "conversation_id": conversation_id})
    with database.session() as session:
        assert [m.content for m in MemoryStore(session, client.user_id).get_messages(conversation_id)] == ["已保存", "测试回复"]


def test_stream_commit_failure_rolls_back(client, database, monkeypatch):
    def fail_commit(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        client.post("/chat/stream", json={"message": "失败"})
    with database.session() as session:
        assert MemoryStore(session, client.user_id).list_conversations() == []
