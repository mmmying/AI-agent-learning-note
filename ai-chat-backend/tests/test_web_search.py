"""联网搜索服务单元测试：模拟 HTTP，验证两个后端的结果解析、降级与配置。"""

import base64
from unittest.mock import MagicMock

import httpx
import pytest

from models.schemas import SearchResult
from services.web_search import WebSearchError, WebSearchService, _clean, _decode_bing_url, build_search_query


def _bing_href(url: str) -> str:
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&u=a1{encoded}&ntb=1"


_BING_HTML = f"""
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="{_bing_href('https://fastapi.tiangolo.com/')}">FastAPI 官方文档</a></h2>
    <div class="b_caption"><p>FastAPI 是一个现代、快速的 Web 框架。</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://www.runoob.com/fastapi/fastapi-tutorial.html">FastAPI 教程 | 菜鸟教程</a></h2>
    <p>FastAPI 入门教程。</p>
  </li>
  <li class="b_algo"><h2>没有链接的条目</h2><p>忽略我</p></li>
</ol>
"""


@pytest.fixture
def http_client(monkeypatch):
    """替换 httpx.Client，返回可在测试里配置响应的假客户端。"""
    client = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = client
    context.__exit__.return_value = False
    factory = MagicMock(return_value=context)
    monkeypatch.setattr("services.web_search.httpx.Client", factory)
    client.factory = factory
    return client


def _response(*, text: str = "", payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.json.return_value = payload or {}
    response.raise_for_status.return_value = None
    return response


@pytest.mark.parametrize(
    "provider,key,expected",
    [
        ("auto", "", "bing"),
        ("auto", "tvly-key", "tavily"),
        ("bing", "tvly-key", "bing"),
        ("tavily", "", "tavily"),
    ],
)
def test_resolve_provider(provider, key, expected):
    service = WebSearchService(provider=provider, api_key=key)
    assert service._resolve_provider() == expected


def test_empty_query_raises():
    with pytest.raises(WebSearchError, match="搜索词为空"):
        WebSearchService(provider="bing").search("   ")


def test_bing_parses_and_decodes_results(http_client):
    http_client.get.return_value = _response(text=_BING_HTML)
    results = WebSearchService(provider="bing").search("FastAPI")

    assert results == [
        SearchResult(
            title="FastAPI 官方文档",
            url="https://fastapi.tiangolo.com/",
            snippet="FastAPI 是一个现代、快速的 Web 框架。",
        ),
        SearchResult(
            title="FastAPI 教程 | 菜鸟教程",
            url="https://www.runoob.com/fastapi/fastapi-tutorial.html",
            snippet="FastAPI 入门教程。",
        ),
    ]
    # 默认直连，不走系统代理
    assert http_client.factory.call_args.kwargs["trust_env"] is False


def test_bing_respects_max_results(http_client):
    http_client.get.return_value = _response(text=_BING_HTML)
    results = WebSearchService(provider="bing", max_results=1).search("FastAPI")
    assert len(results) == 1
    assert http_client.get.call_args.kwargs["params"]["count"] == 1


def test_bing_no_results_returns_empty(http_client):
    http_client.get.return_value = _response(text="<ol id='b_results'></ol>")
    assert WebSearchService(provider="bing").search("冷门词") == []


def test_bing_network_error_wrapped(http_client):
    http_client.get.side_effect = httpx.ConnectTimeout("timeout")
    with pytest.raises(WebSearchError, match="联网搜索暂时不可用"):
        WebSearchService(provider="bing").search("FastAPI")


def test_tavily_parses_results(http_client):
    http_client.post.return_value = _response(
        payload={
            "results": [
                {"title": "FastAPI", "url": "https://fastapi.tiangolo.com/", "content": "官方文档"},
                {"title": "", "url": "https://skip.invalid", "content": "无标题跳过"},
            ]
        }
    )
    results = WebSearchService(provider="tavily", api_key="tvly-key").search("FastAPI")
    assert results == [
        SearchResult(title="FastAPI", url="https://fastapi.tiangolo.com/", snippet="官方文档")
    ]
    headers = http_client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer tvly-key"


def test_tavily_without_key_raises():
    with pytest.raises(WebSearchError, match="未配置 TAVILY_API_KEY"):
        WebSearchService(provider="tavily", api_key="").search("FastAPI")


def test_tavily_network_error_wrapped(http_client):
    http_client.post.side_effect = httpx.ConnectError("boom")
    with pytest.raises(WebSearchError, match="联网搜索暂时不可用"):
        WebSearchService(provider="tavily", api_key="tvly-key").search("FastAPI")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/page", "https://example.com/page"),
        (_bing_href("https://fastapi.tiangolo.com/"), "https://fastapi.tiangolo.com/"),
        ("https://www.bing.com/ck/a?!&&u=zzz", "https://www.bing.com/ck/a?!&&u=zzz"),
    ],
)
def test_decode_bing_url(url, expected):
    assert _decode_bing_url(url) == expected


def test_clean_collapses_whitespace():
    assert _clean("  FastAPI\t 教程\n 文档 ") == "FastAPI 教程 文档"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("介绍下deepseek harness", "deepseek harness"),
        ("介绍一下 deepseek harness", "deepseek harness"),
        ("FastAPI 是什么", "FastAPI"),
        ("什么是 FastAPI", "FastAPI"),
        ("请问 deepseek harness 怎么样", "deepseek harness"),
        ("帮我查一下 上海天气", "上海天气"),
        ("deepseek harness", "deepseek harness"),
        ("what is deepseek harness", "deepseek harness"),
        ("介绍信怎么写", "介绍信怎么写"),  # 裸动词不剥，避免误伤主题词
        ("搜索引擎的工作原理", "搜索引擎的工作原理"),
        ("吗", "吗"),  # 剥空时退回原消息
    ],
)
def test_build_search_query(message, expected):
    assert build_search_query(message) == expected