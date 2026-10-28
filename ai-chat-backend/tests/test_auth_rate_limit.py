from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from core.config import Settings, settings
from core.security import password_hash
from db.database import Database
from db.orm_models import AuthRateLimit, User
from fastapi import HTTPException
from services.auth_rate_limit import enforce_auth_rate_limit
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.api, pytest.mark.database]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.5]
    monkeypatch.setattr("services.auth_rate_limit.time", lambda: now[0])
    return now


@pytest.mark.parametrize("action", ["register", "login"])
def test_limit_counts_failures_and_resets_after_window(
    client, database, monkeypatch, clock, action
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
    clock[0] = 1010.75
    blocked = client.post(f"/auth/{action}", json=body)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "50"
    assert blocked.json() == {"detail": "该账号请求过于频繁，请稍后再试"}
    with database.session() as session:
        bucket = session.get(AuthRateLimit, (action, username))
        assert bucket.attempts == 2
        assert bucket.expires_at == 1060.5
    # 超限请求不会延长窗口；到期时即可继续。
    clock[0] = 1060.5
    assert client.post(f"/auth/{action}", json=body).status_code == (
        409 if action == "register" else 401
    )


def test_login_limit_counts_success_and_normalizes_username(client, monkeypatch, clock):
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


def test_accounts_and_actions_have_independent_limits(client, monkeypatch, clock):
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


def test_rate_limited_request_does_not_hash_or_verify_password(
    client, monkeypatch, clock
):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    body = {"username": "test_user", "password": "wrong-password"}
    assert client.post("/auth/login", json=body).status_code == 401
    verify = Mock(wraps=password_hash.verify)
    monkeypatch.setattr(password_hash, "verify", verify)
    assert client.post("/auth/login", json=body).status_code == 429
    verify.assert_not_called()


def test_invalid_input_does_not_consume_rate_limit(client, monkeypatch, clock):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 1)
    assert (
        client.post(
            "/auth/login", json={"username": "test_user", "password": "short"}
        ).status_code
        == 422
    )
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


def test_rate_limit_is_shared_by_database_instances_and_concurrent_requests(
    database, monkeypatch, clock
):
    monkeypatch.setattr(settings, "auth_login_rate_limit", 3)
    second = Database(database.path)
    second.engine.echo = False

    def attempt(index):
        try:
            enforce_auth_rate_limit(
                database if index % 2 else second, "concurrent_user", "login"
            )
            return 200
        except HTTPException as error:
            return error.status_code

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        assert results.count(200) == 3
        assert results.count(429) == 5
    finally:
        second.engine.dispose()
    # 重新创建 Engine 仍沿用已有计数。
    reopened = Database(database.path)
    try:
        with pytest.raises(HTTPException) as error:
            enforce_auth_rate_limit(reopened, "concurrent_user", "login")
        assert error.value.status_code == 429
    finally:
        reopened.engine.dispose()


def test_expired_accounts_are_cleaned_up(database, monkeypatch, clock):
    enforce_auth_rate_limit(database, "expired_user", "login")
    clock[0] += settings.auth_login_rate_window_seconds
    enforce_auth_rate_limit(database, "fresh_user", "login")
    with database.session() as session:
        assert session.get(AuthRateLimit, ("login", "expired_user")) is None
        assert session.get(AuthRateLimit, ("login", "fresh_user")) is not None


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
