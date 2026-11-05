from concurrent.futures import ThreadPoolExecutor
from time import sleep
from unittest.mock import Mock

import pytest
import redis
from core.config import Settings, settings
from core.security import password_hash
from db.orm_models import User
from fastapi import HTTPException
from services.auth_rate_limit import enforce_auth_rate_limit
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.api, pytest.mark.database]


def limit_key(action: str, username: str) -> str:
    return f"auth:rate-limit:{action}:{username.strip().lower()}"


@pytest.fixture
def shared_server():
    """一套被多个客户端共享的内存 Redis，模拟多进程共用同一个服务。"""
    import fakeredis

    return fakeredis.FakeServer()


@pytest.mark.parametrize("action", ["register", "login"])
def test_limit_counts_failures_and_resets_after_window(
    client, redis_client, monkeypatch, action
):
    monkeypatch.setattr(settings, f"auth_{action}_rate_limit", 2)
    monkeypatch.setattr(settings, f"auth_{action}_rate_window_seconds", 60)
    username = "limited_account" if action == "register" else "test_user"
    body = {"username": username, "password": "wrong-password"}
    first = client.post(f"/auth/{action}", json=body)
    assert first.status_code == (201 if action == "register" else 401)
    assert client.post(f"/auth/{action}", json=body).status_code == (
        409 if action == "register" else 401
    )
    blocked = client.post(f"/auth/{action}", json=body)
    assert blocked.status_code == 429
    # 窗口从首次请求开始，还剩约 60 秒。
    assert 50 <= int(blocked.headers["retry-after"]) <= 60
    assert blocked.json() == {"detail": "该账号请求过于频繁，请稍后再试"}
    remaining = redis_client.pttl(limit_key(action, username))
    assert remaining > 50_000
    # 超限请求不会延长窗口。
    assert client.post(f"/auth/{action}", json=body).status_code == 429
    assert redis_client.pttl(limit_key(action, username)) <= remaining


def test_window_expiry_restores_the_allowance(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    monkeypatch.setattr(settings, "auth_login_rate_window_seconds", 1)
    body = {"username": "test_user", "password": "test-password-123"}
    assert client.post("/auth/login", json=body).status_code == 200
    assert client.post("/auth/login", json=body).status_code == 429
    # 等窗口自然过期，而不是删键来伪造。
    sleep(1.2)
    assert client.post("/auth/login", json=body).status_code == 200


def test_login_limit_counts_success_and_normalizes_username(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    success = client.post(
        "/auth/login", json={"username": " TEST_USER ", "password": "test-password-123"}
    )
    assert success.status_code == 200
    assert (
        client.post(
            "/auth/login",
            json={"username": "test_user", "password": "test-password-123"},
        ).status_code
        == 429
    )
    assert client.get("/auth/me").status_code == 200


def test_accounts_and_actions_have_independent_limits(client, redis_client, monkeypatch):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    for username in ["first_unknown", "second_unknown"]:
        body = {"username": username, "password": "password-123"}
        assert client.post("/auth/login", json=body).status_code == 401
        assert client.post("/auth/login", json=body).status_code == 429
    assert (
        client.post(
            "/auth/register",
            json={"username": "first_unknown", "password": "password-123"},
        ).status_code
        == 201
    )
    assert redis_client.get(limit_key("register", "first_unknown")) == "1"


def test_rate_limited_request_does_not_hash_or_verify_password(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    body = {"username": "test_user", "password": "wrong-password"}
    assert client.post("/auth/login", json=body).status_code == 401
    verify = Mock(wraps=password_hash.verify)
    monkeypatch.setattr(password_hash, "verify", verify)
    assert client.post("/auth/login", json=body).status_code == 429
    verify.assert_not_called()


def test_invalid_input_does_not_consume_rate_limit(client, redis_client, monkeypatch):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    assert (
        client.post(
            "/auth/login", json={"username": "test_user", "password": "short"}
        ).status_code
        == 422
    )
    assert redis_client.get(limit_key("login", "test_user")) is None
    assert (
        client.post(
            "/auth/login",
            json={"username": "test_user", "password": "test-password-123"},
        ).status_code
        == 200
    )


def test_duplicate_username_is_checked_before_hashing(client, database, monkeypatch):
    hash_password = Mock(wraps=password_hash.hash)
    monkeypatch.setattr(password_hash, "hash", hash_password)
    response = client.post(
        "/auth/register",
        json={"username": " TEST_USER ", "password": "another-password"},
    )
    assert response.status_code == 409
    hash_password.assert_not_called()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_database_unique_constraint_handles_registration_race(
    client, database, monkeypatch
):
    original_hash = password_hash.hash

    def race_hash(password):
        # 预检查之后、当前请求提交之前，另一个连接注册了相同用户名。
        with database.session() as session:
            session.add(
                User(
                    id="race-winner",
                    username="race_user",
                    password_hash=original_hash(password),
                )
            )
            session.commit()
        return original_hash(password)

    monkeypatch.setattr(password_hash, "hash", race_hash)
    response = client.post(
        "/auth/register", json={"username": "race_user", "password": "password-123"}
    )
    assert response.status_code == 409
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.username == "race_user")
            )
            == 1
        )


def test_unrelated_integrity_error_is_not_reported_as_duplicate_username(
    client, monkeypatch
):
    monkeypatch.setattr("api.auth.uuid4", lambda: client.user_id)
    with pytest.raises(IntegrityError):
        client.post(
            "/auth/register",
            json={"username": "new_username", "password": "password-123"},
        )


def test_rate_limit_is_shared_across_clients_and_concurrent_requests(
    shared_server, monkeypatch
):
    import fakeredis

    monkeypatch.setattr(settings, "auth_login_rate_limit", 3)
    clients = [fakeredis.FakeStrictRedis(server=shared_server) for _ in range(2)]

    def attempt(index):
        try:
            enforce_auth_rate_limit(
                clients[index % 2], "concurrent_user", "login"
            )
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(200) == 3
    assert results.count(429) == 5
    # 新建客户端连接同一个 Redis 仍沿用已有计数。
    with pytest.raises(HTTPException) as error:
        enforce_auth_rate_limit(
            fakeredis.FakeStrictRedis(server=shared_server), "concurrent_user", "login"
        )
    assert error.value.status_code == 429


def test_key_without_expiry_gets_a_window(shared_server, monkeypatch):
    """进程在累加后崩溃会留下没有过期时间的键，下次请求要补上窗口。"""
    import fakeredis

    monkeypatch.setattr(settings, "auth_login_rate_limit", 3)
    monkeypatch.setattr(settings, "auth_login_rate_window_seconds", 45)
    client = fakeredis.FakeStrictRedis(server=shared_server)
    client.set(limit_key("login", "orphan_user"), "5")
    with pytest.raises(HTTPException) as error:
        enforce_auth_rate_limit(client, "orphan_user", "login")
    assert error.value.status_code == 429
    assert 0 < client.pttl(limit_key("login", "orphan_user")) <= 45_000


@pytest.mark.parametrize(
    "client_error",
    [redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
)
def test_unreachable_redis_fails_closed_with_503(client_error):
    """Redis 不可用时无法计数，宁可返回可读的 503，也不放行未限流的认证请求。"""
    broken = Mock()
    broken.set.side_effect = client_error("redis is down")
    with pytest.raises(HTTPException) as error:
        enforce_auth_rate_limit(broken, "any_user", "login")
    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "5"}
    broken.incr.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "auth_register_rate_limit",
        "auth_login_rate_limit",
        "auth_register_rate_window_seconds",
        "auth_login_rate_window_seconds",
    ],
)
def test_rate_limit_configuration_rejects_zero(field):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: 0})
