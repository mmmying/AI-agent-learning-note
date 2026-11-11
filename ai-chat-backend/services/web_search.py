"""联网搜索：把用户问题交给搜索引擎，取回标题/链接/摘要供模型参考。

提供两个后端：Tavily（需 TAVILY_API_KEY，结果面向 LLM 优化）和 Bing 网页搜索
（免密钥，解析 HTML）。search_provider=auto 时优先 Tavily，未配密钥则用 Bing。
任何失败都抛 WebSearchError，由调用方降级为不带搜索的普通回复。
"""

import base64
import binascii
import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx
from core.config import settings
from lxml import html
from models.schemas import SearchResult

logger = logging.getLogger(__name__)

_BING_ENDPOINT = "https://www.bing.com/search"
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class WebSearchError(Exception):
    """联网搜索失败，调用方据此降级为普通回复。"""


class WebSearchService:
    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        max_results: int | None = None,
        timeout: int | None = None,
        trust_env: bool | None = None,
    ) -> None:
        self.provider = provider or settings.search_provider
        self.api_key = api_key if api_key is not None else settings.tavily_api_key
        self.max_results = max_results or settings.search_max_results
        self.timeout = timeout or settings.search_timeout
        self.trust_env = settings.search_trust_env if trust_env is None else trust_env

    def search(self, query: str) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise WebSearchError("搜索词为空")

        provider = self._resolve_provider()
        try:
            if provider == "tavily":
                return self._search_tavily(query)
            return self._search_bing(query)
        except WebSearchError:
            raise
        except Exception as exc:
            logger.warning("联网搜索失败 provider=%s query=%r: %s", provider, query, exc)
            raise WebSearchError("联网搜索暂时不可用") from exc

    def _resolve_provider(self) -> str:
        if self.provider != "auto":
            return self.provider
        return "tavily" if self.api_key else "bing"

    def _search_tavily(self, query: str) -> list[SearchResult]:
        if not self.api_key:
            raise WebSearchError("未配置 TAVILY_API_KEY")

        with httpx.Client(trust_env=self.trust_env, timeout=self.timeout) as client:
            response = client.post(
                _TAVILY_ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "query": query,
                    "max_results": self.max_results,
                    "search_depth": "basic",
                    "include_answer": False,
                },
            )
        response.raise_for_status()
        payload = response.json()

        results: list[SearchResult] = []
        for item in payload.get("results", []):
            url = item.get("url", "")
            title = _clean(item.get("title", ""))
            if not url or not title:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=_clean(item.get("content", "")),
                )
            )
        return results[: self.max_results]

    def _search_bing(self, query: str) -> list[SearchResult]:
        with httpx.Client(
            trust_env=self.trust_env,
            follow_redirects=True,
            timeout=self.timeout,
        ) as client:
            response = client.get(
                _BING_ENDPOINT,
                params={"q": query, "count": self.max_results},
                headers=_BROWSER_HEADERS,
            )
        response.raise_for_status()
        document = html.fromstring(response.text)

        results: list[SearchResult] = []
        for node in document.xpath('//li[@class="b_algo"]'):
            anchors = node.xpath(".//h2/a[@href]")
            if not anchors:
                continue
            anchor = anchors[0]
            url = _decode_bing_url(anchor.get("href", ""))
            title = _clean(anchor.text_content())
            if not url or not title:
                continue
            snippets = node.xpath('.//div[contains(@class,"b_caption")]//p') or node.xpath(".//p")
            snippet = _clean(snippets[0].text_content()) if snippets else ""
            results.append(SearchResult(title=title, url=url, snippet=snippet))
            if len(results) >= self.max_results:
                break
        return results


def _decode_bing_url(url: str) -> str:
    # Bing 把真实地址 base64 编码在 /ck/a 跳转链接的 u=a1... 参数里
    if "bing.com/ck/" not in url:
        return url
    raw = parse_qs(urlparse(url).query).get("u", [""])[0]
    if not raw.startswith("a1"):
        return url
    try:
        return base64.urlsafe_b64decode(raw[2:] + "==").decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return url


def _clean(text: str) -> str:
    return " ".join(text.split())


# 口语整句直接当搜索词会被 filler 带偏（如“介绍下deepseek harness”搜成“介绍”词典），
# 这里剥掉祈使/疑问外壳，留下真正要查的主题词。
_LEADING_FILLER = (
    "请问一下", "请帮我查一下", "请帮我", "请问",
    "帮我查一下", "帮我查下", "帮我查查", "帮我查", "帮我看看", "帮我",
    "帮忙查一下", "帮忙查下", "帮忙",
    "告诉我一下", "告诉我",
    "介绍一下", "介绍下",
    "简述一下", "简述下",
    "解释一下", "解释下",
    "说明一下", "说明下",
    "分析一下", "分析下",
    "对比一下", "对比下",
    "推荐一下", "推荐下",
    "总结一下", "总结下",
    "了解一下", "了解下",
    "讲一下", "讲下", "讲讲",
    "说一说", "说说",
    "聊一聊", "聊一下",
    "查一下", "查下", "查查",
    "查询一下", "查询下",
    "查找一下", "查找下",
    "搜索一下", "搜索下",
    "搜一下", "搜下",
    "什么是", "何为",
)
_TRAILING_FILLER = (
    "是什么意思", "什么意思", "是什么",
    "怎么样", "怎样", "如何",
    "好不好", "好吗",
    "一下", "下",
    "吗", "呢", "吧", "啊", "呀",
    "？", "?", "。", "！", "!", "，", ",",
)
_EN_LEADING_FILLER = (
    "please ", "tell me ", "explain ", "introduce ",
    "what is ", "what are ", "what's ",
    "search for ", "look up ", "can you ", "could you ",
)
_EN_TRAILING_FILLER = (" please", "?", ".")


def build_search_query(message: str) -> str:
    """把用户消息收敛成搜索主题词；剥不动或剥空时退回原消息。"""
    original = _clean(message)
    query = original
    for _ in range(6):
        stripped = _strip_once(query, _LEADING_FILLER, _EN_LEADING_FILLER, leading=True)
        if stripped == query:
            break
        query = stripped
    for _ in range(6):
        stripped = _strip_once(query, _TRAILING_FILLER, _EN_TRAILING_FILLER, leading=False)
        if stripped == query:
            break
        query = stripped
    query = query.strip(" \t　、，,。.")
    return query if len(query) >= 2 else original


def _strip_once(query: str, cn: tuple[str, ...], en: tuple[str, ...], *, leading: bool) -> str:
    lowered = query.lower()
    for filler in cn:
        if len(query) > len(filler) and (query.startswith(filler) if leading else query.endswith(filler)):
            return (query[len(filler):] if leading else query[: -len(filler)]).strip()
    for filler in en:
        if len(query) > len(filler) and (lowered.startswith(filler) if leading else lowered.endswith(filler)):
            return (query[len(filler):] if leading else query[: -len(filler)]).strip()
    return query


# 比较句的连接词与结尾比较词；仅用于改写失败时的启发式拆分回退。
_ENTITY_CONNECTORS = ("和", "与", "跟", "、", " vs ", " versus ", " and ", "&")
_COMPARISON_TAIL = (
    "什么区别", "的区别", "区别", "什么差异", "的差异", "差异",
    "有什么不同", "不同", "哪个好", "哪个更好", "对比", "比较", "什么",
)


def split_search_entities(query: str) -> list[str]:
    """把比较句按连接词拆成多个实体查询词；单实体原样返回。

    Bing 对「A和B区别」这类多实体组合查询召回极差（退化成泛泛公司页），
    但单独搜每个实体都很准，故拆开后分别搜索再合并。
    """
    parts = [query]
    for connector in _ENTITY_CONNECTORS:
        parts = [piece for part in parts for piece in part.split(connector)]
    entities: list[str] = []
    for part in parts:
        part = part.strip()
        for tail in _COMPARISON_TAIL:
            if len(part) > len(tail) and part.endswith(tail):
                part = part[: -len(tail)].strip()
                break
        if part and part not in entities:
            entities.append(part)
    return entities or [query]


def results_mention_query(query: str, results: list[SearchResult]) -> bool:
    """判断搜索结果是否真的与查询词相关（任一标题/链接/摘要提及查询词元）。

    Bing 对裸品牌词偶发返回无关门户/缓存垃圾页（如京东、ChatGPT 首页），
    这类结果不含查询词元，据此识别噪声 SERP 以便重试或丢弃。
    """
    tokens = [t for t in re.split(r"[^0-9a-z\u4e00-\u9fff]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return bool(results)
    for result in results:
        haystack = f"{result.title} {result.url} {result.snippet}".lower()
        if any(token in haystack for token in tokens):
            return True
    return False