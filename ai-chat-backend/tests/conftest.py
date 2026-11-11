"""pytest 共用隔离环境；在测试模块导入应用之前替换本地配置。"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

_temp_parent = Path(__file__).resolve().parents[1] / ".test-tmp"
_temp_parent.mkdir(exist_ok=True)
# 每次运行单独创建目录，避免复用系统 pytest-of-* 的权限和锁文件。
_test_root = tempfile.TemporaryDirectory(prefix="run-", dir=_temp_parent)
_temp_directory = patch.object(tempfile, "tempdir", _test_root.name)
_temp_directory.start()
_environment = patch.dict(
    os.environ,
    {
        "APP_ENV": "test",
        "DB_PATH": os.path.join(_test_root.name, "default.db"),
        "DEBUG": "false",
        "OPENAI_API_KEY": "test-openai-key",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "TEMP": _test_root.name,
        "TMP": _test_root.name,
        "TMPDIR": _test_root.name,
    },
)
_environment.start()


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # 在 pytest 创建 TempPathFactory 前设置；保留命令行 --basetemp 的优先级。
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(_test_root.name) / "pytest")


def pytest_unconfigure(config):
    # 默认 Engine 可能被未来的测试使用，先释放连接再清理临时目录。
    import sys

    if "db.database" in sys.modules:
        sys.modules["db.database"].database.engine.dispose()
    _environment.stop()
    _temp_directory.stop()
    _test_root.cleanup()


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    """TestClient 使用独立 transport；真实 HTTP/AI 请求应立即报错。"""

    def blocked(*args, **kwargs):
        raise AssertionError("测试禁止真实 HTTP 请求，请模拟 AI 客户端")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


@pytest.fixture
def database(tmp_path):
    from db.database import Database, init_db

    db = Database(tmp_path / "chat.db")
    try:
        init_db(db)
        yield db
    finally:
        db.engine.dispose()


@pytest.fixture
def db_session(database):
    with database.session() as session:
        yield session


@pytest.fixture
def memory_store(db_session):
    from services.memory_store import MemoryStore

    return MemoryStore(db_session)


@pytest.fixture
def ai_client(monkeypatch):
    from models.schemas import TokenUsage
    from services.ai_client import AIClient

    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    client = MagicMock(spec=AIClient)
    client.chat.return_value = ("测试回复", usage)

    def fake_stream(**kwargs):
        yield "测试"
        yield "回复"
        return usage

    client.chat_stream.side_effect = fake_stream
    monkeypatch.setattr("services.chat_service.AIClient", lambda **kwargs: client)
    return client


@pytest.fixture
def web_search(monkeypatch):
    """ mock 掉 ChatService 里的搜索服务，默认返回两条结果。"""
    from models.schemas import SearchResult
    from services.web_search import WebSearchService

    service = MagicMock(spec=WebSearchService)
    service.max_results = 5
    service.search.return_value = [
        SearchResult(title="FastAPI 官方文档", url="https://fastapi.tiangolo.com/", snippet="现代 Web 框架"),
        SearchResult(title="FastAPI 教程", url="https://www.runoob.com/fastapi/", snippet="入门教程"),
    ]
    monkeypatch.setattr("services.chat_service.WebSearchService", lambda *a, **k: service)
    return service


@pytest.fixture
def redis_client():
    """每个测试一套内存 Redis，限流计数不跨测试残留。"""
    import fakeredis

    client = fakeredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        client.flushall()
        client.close()


@pytest.fixture
def app(database, redis_client, monkeypatch):
    import main

    monkeypatch.setattr(main, "database", database)
    monkeypatch.setattr(main, "create_redis_client", lambda url: redis_client)
    previous_overrides = main.app.dependency_overrides.copy()
    main.app.dependency_overrides.clear()
    try:
        yield main.app
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous_overrides)


@pytest.fixture
def client(app, ai_client):
    from fastapi.testclient import TestClient

    # 进入上下文，真实执行应用的数据库迁移与关闭生命周期。
    with TestClient(app) as test_client:
        response = test_client.post("/auth/register", json={
            "username": "test_user", "password": "test-password-123",
        })
        assert response.status_code == 201
        test_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        test_client.user_id = response.json()["user"]["id"]
        yield test_client
