"""真实 JWT 与密码验证，覆盖跨用户读写和旧数据隔离。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException

from core.config import Settings, settings
from core.security import password_hash
from db.orm_models import User
from models.schemas import Message
from services.memory_store import MemoryStore

pytestmark = [pytest.mark.api, pytest.mark.database]


def register(client, username="another_user"):
    response = client.post("/auth/register", json={"username": username, "password": "password-123"})
    assert response.status_code == 201
    return response.json()


def test_register_login_and_me(client, database):
    auth = register(client, " Alice_01 ")
    assert auth["token_type"] == "bearer"
    assert auth["user"]["username"] == "alice_01"
    assert set(auth["user"]) == {"id", "username", "created_at"}
    with database.session() as session:
        user = session.get(User, auth["user"]["id"])
        assert user.password_hash != "password-123"
        assert password_hash.verify("password-123", user.password_hash)
    login = client.post("/auth/login", json={"username": "ALICE_01", "password": "password-123"})
    assert login.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.json() == auth["user"]
    assert client.post("/auth/register", json={"username": "alice_01", "password": "another-password"}).status_code == 409


@pytest.mark.parametrize("username,password", [("xx", "password-123"), ("bad name", "password-123"),
                                                ("valid_name", "short"), ("a" * 33, "password-123"),
                                                ("valid_name", "x" * 129)])
def test_registration_validation(client, username, password):
    assert client.post("/auth/register", json={"username": username, "password": password}).status_code == 422


@pytest.mark.parametrize("username", ["test_user", "unknown_user"])
def test_login_wrong_credentials(client, username):
    response = client.post("/auth/login", json={"username": username, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/auth/me", None), ("GET", "/conversations", None),
    ("GET", "/conversations/other/messages", None), ("DELETE", "/conversations/other", None),
    ("POST", "/chat/chat", {"message": "hello"}), ("POST", "/chat/stream", {"message": "hello"}),
])
def test_protected_endpoints_require_bearer(client, method, path, body):
    client.headers.pop("Authorization")
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("kind", ["expired", "tampered", "missing_exp", "wrong_algorithm", "deleted_user", "malformed"])
def test_invalid_tokens_are_rejected(client, kind):
    payload = jwt.decode(client.headers["Authorization"].split()[1], settings.jwt_secret_key,
                         algorithms=["HS256"], audience="ai-chat-frontend", issuer="ai-chat-backend")
    key = settings.jwt_secret_key
    algorithm = "HS256"
    if kind == "expired":
        payload["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif kind == "tampered":
        key = "another-secret-at-least-32-characters-long"
    elif kind == "missing_exp":
        del payload["exp"]
    elif kind == "wrong_algorithm":
        algorithm = "HS384"
    elif kind == "deleted_user":
        payload["sub"] = str(uuid4())
    token = "invalid.token" if kind == "malformed" else jwt.encode(payload, key, algorithm=algorithm)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_user_cannot_read_delete_or_continue_another_users_conversation(client, database, ai_client):
    owner_headers = dict(client.headers)
    conversation_id = client.post("/chat/chat", json={"message": "私密消息"}).json()["conversation_id"]
    other = register(client)
    client.headers["Authorization"] = f"Bearer {other['access_token']}"
    ai_client.reset_mock()
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{conversation_id}/messages").status_code == 404
    assert client.delete(f"/conversations/{conversation_id}").status_code == 404
    for endpoint in ["/chat/chat", "/chat/stream"]:
        response = client.post(endpoint, json={"message": "越权写入", "conversation_id": conversation_id})
        assert response.status_code == 404
        assert "text/event-stream" not in response.headers.get("content-type", "")
    ai_client.chat.assert_not_called()
    ai_client.chat_stream.assert_not_called()
    # 第二个用户仍能创建自己的对话，返回第一个账号后只能看到原有记录。
    second_id = client.post("/chat/chat", json={"message": "第二个用户"}).json()["conversation_id"]
    assert [c["id"] for c in client.get("/conversations").json()] == [second_id]
    assert [m["content"] for m in client.get(f"/conversations/{conversation_id}/messages", headers=owner_headers).json()] == ["私密消息", "测试回复"]
    assert [c["id"] for c in client.get("/conversations", headers=owner_headers).json()] == [conversation_id]


def test_unassigned_history_is_preserved_and_hidden(client, database):
    with database.session() as session:
        MemoryStore(session).add_message("legacy", Message(role="user", content="升级前的消息"))
        session.commit()
    assert client.get("/conversations").json() == []
    assert client.get("/conversations/legacy/messages").status_code == 404
    assert client.delete("/conversations/legacy").status_code == 404
    for endpoint in ["/chat/chat", "/chat/stream"]:
        assert client.post(endpoint, json={"conversation_id": "legacy", "message": "尝试认领"}).status_code == 404
    with database.session() as session:
        assert MemoryStore(session).get_messages("legacy")[0].content == "升级前的消息"


def test_store_enforces_ownership_even_without_route_checks(client, database):
    conversation_id = client.post("/chat/chat", json={"message": "private"}).json()["conversation_id"]
    other = register(client)
    with database.session() as session:
        store = MemoryStore(session, other["user"]["id"])
        assert store.get_messages(conversation_id) == []
        assert not store.conversation_exists(conversation_id)
        assert not store.delete_conversation(conversation_id)
        with pytest.raises(HTTPException) as error:
            store.add_message(conversation_id, Message(role="user", content="forbidden"))
        assert error.value.status_code == 404
        session.rollback()
    assert len(client.get(f"/conversations/{conversation_id}/messages").json()) == 2


@pytest.mark.parametrize("endpoint", ["/chat/chat", "/chat/stream"])
def test_unknown_conversation_does_not_create_arbitrary_id(client, endpoint):
    assert client.post(endpoint, json={"conversation_id": "invented", "message": "hello"}).status_code == 404


def test_production_requires_configured_secret():
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, app_env="production")
    with pytest.raises(ValueError):
        Settings(_env_file=None, jwt_secret_key="short")
