"""聊天接口接入联网搜索的集成测试：SSE 事件、提示词注入、失败降级与落库。"""

import json
from unittest.mock import call

import pytest

from models.schemas import SearchResult, TokenUsage
from services.chat_service import _clean_extracted_query, _parse_extracted_queries
from services.memory_store import MemoryStore
from services.web_search import WebSearchError, results_mention_query, split_search_entities

pytestmark = [pytest.mark.api, pytest.mark.database]


def _events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_web_search_events_and_prompt(client, ai_client, web_search, database):
    ai_client.chat.return_value = ("FastAPI", TokenUsage())
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    assert response.status_code == 200
    events = _events(response)
    conversation_id = events[0]["conversation_id"]

    assert [event["type"] for event in events] == [
        "start", "search_start", "search_result", "delta", "delta", "done",
    ]
    assert events[1]["query"] == "FastAPI"
    assert events[2]["query"] == "FastAPI"
    assert events[2]["results"] == [
        {"title": "FastAPI 官方文档", "url": "https://fastapi.tiangolo.com/", "snippet": "现代 Web 框架"},
        {"title": "FastAPI 教程", "url": "https://www.runoob.com/fastapi/", "snippet": "入门教程"},
    ]
    web_search.search.assert_called_once_with("FastAPI")
    # 流式路径下 chat 用于改写 + 补搜审查两次；审查回显第一轮查询词被去重，不触发补搜
    assert ai_client.chat.call_count == 2

    # 搜索结果与引用要求注入系统提示词，但发给模型的历史消息保持干净
    prompt = ai_client.chat_stream.call_args.kwargs["system_prompt"]
    assert "联网搜索参考" in prompt
    assert "https://fastapi.tiangolo.com/" in prompt
    assert "Markdown 链接标注来源" in prompt
    assert [m.content for m in ai_client.chat_stream.call_args.kwargs["messages"]] == ["FastAPI 是什么"]

    # 搜索上下文不写入会话记忆，只留下用户消息与助手回复
    with database.session() as session:
        stored = MemoryStore(session, client.user_id).get_messages(conversation_id)
        assert [m.content for m in stored] == ["FastAPI 是什么", "测试回复"]


def test_stream_search_query_uses_llm_rewrite(client, ai_client, web_search):
    # 回归：词表剥不动的口语（“我不懂deepseek harness”）交给模型改写成主题词
    ai_client.chat.return_value = ("deepseek harness", TokenUsage())
    web_search.search.return_value = [
        SearchResult(title="DeepSeek Harness 官方", url="https://www.deepseek.com/harness/", snippet="dsh"),
    ]
    response = client.post("/chat/stream", json={"message": "我不懂deepseek harness", "web_search": True})
    assert response.status_code == 200
    events = _events(response)
    assert events[2]["query"] == "deepseek harness"
    web_search.search.assert_called_once_with("deepseek harness")


def test_stream_search_query_falls_back_when_rewrite_fails(client, ai_client, web_search):
    # 改写调用失败时回退启发式剥离，仍能得到主题词且不阻断回复
    ai_client.chat.side_effect = RuntimeError("model down")
    web_search.search.return_value = [
        SearchResult(title="DeepSeek Harness 官方", url="https://www.deepseek.com/harness/", snippet="dsh"),
    ]
    response = client.post("/chat/stream", json={"message": "介绍下deepseek harness", "web_search": True})
    assert response.status_code == 200
    events = _events(response)
    assert events[2]["query"] == "deepseek harness"
    web_search.search.assert_called_once_with("deepseek harness")
    assert events[-1]["type"] == "done"


def test_stream_search_query_empty_rewrite_falls_back(client, ai_client, web_search):
    ai_client.chat.return_value = ("   \n  ", TokenUsage())
    web_search.search.return_value = [
        SearchResult(title="DeepSeek Harness 官方", url="https://www.deepseek.com/harness/", snippet="dsh"),
    ]
    response = client.post("/chat/stream", json={"message": "介绍下deepseek harness", "web_search": True})
    events = _events(response)
    assert events[2]["query"] == "deepseek harness"
    web_search.search.assert_called_once_with("deepseek harness")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deepseek harness", "deepseek harness"),
        ('  "deepseek harness"  ', "deepseek harness"),
        ("“deepseek harness”。", "deepseek harness"),
        ("deepseek harness\n解释：……", "deepseek harness"),
        ("   ", ""),
    ],
)
def test_clean_extracted_query(raw, expected):
    assert _clean_extracted_query(raw) == expected


def test_stream_web_search_off_skips_search(client, ai_client, web_search):
    response = client.post("/chat/stream", json={"message": "你好"})
    assert [event["type"] for event in _events(response)] == ["start", "delta", "delta", "done"]
    web_search.search.assert_not_called()
    ai_client.chat.assert_not_called()
    assert "联网搜索参考" not in ai_client.chat_stream.call_args.kwargs["system_prompt"]


def test_stream_search_failure_degrades_to_normal(client, ai_client, web_search):
    web_search.search.side_effect = WebSearchError("联网搜索暂时不可用")
    response = client.post("/chat/stream", json={"message": "你好", "web_search": True})
    assert response.status_code == 200
    events = _events(response)
    assert [event["type"] for event in events] == [
        "start", "search_start", "search_error", "delta", "delta", "done",
    ]
    assert events[2]["message"] == "联网搜索暂时不可用"
    # 搜索失败不影响回复，已成功调用的搜索词改写仍计入用量。
    assert events[-1]["usage"]["total_tokens"] == 30
    assert "联网搜索参考" not in ai_client.chat_stream.call_args.kwargs["system_prompt"]


def test_stream_search_empty_results_reports_error(client, web_search):
    web_search.search.return_value = []
    response = client.post("/chat/stream", json={"message": "冷门问题", "web_search": True})
    events = _events(response)
    assert events[2]["type"] == "search_error"
    assert events[2]["message"] == "未找到相关搜索结果"
    assert events[-1]["type"] == "done"


def test_chat_non_stream_returns_sources(client, ai_client, web_search):
    # 非流式下 chat 依次用于改写、补搜审查、回复，三次返回不同内容
    ai_client.chat.side_effect = [
        ("FastAPI", TokenUsage()),
        ("ENOUGH", TokenUsage()),
        ("测试回复", TokenUsage()),
    ]
    response = client.post("/chat/chat", json={"message": "FastAPI 是什么", "web_search": True})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "测试回复"
    assert len(body["sources"]) == 2
    assert body["sources"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert "联网搜索参考" in ai_client.chat.call_args.kwargs["system_prompt"]


def test_chat_non_stream_without_search_has_empty_sources(client, web_search):
    response = client.post("/chat/chat", json={"message": "你好"})
    assert response.status_code == 200
    assert response.json()["sources"] == []
    web_search.search.assert_not_called()


def test_stream_sources_persisted_for_history(client, ai_client, web_search):
    # 切换会话后历史仍要能看到来源：来源与搜索词随助手消息落库并经消息接口返回
    ai_client.chat.return_value = ("FastAPI", TokenUsage())
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    conversation_id = _events(response)[0]["conversation_id"]

    history = client.get(f"/conversations/{conversation_id}/messages")
    assert history.status_code == 200
    messages = history.json()
    assert messages[0]["sources"] == []
    assistant = messages[-1]
    assert assistant["role"] == "assistant"
    assert len(assistant["sources"]) == 2
    assert assistant["sources"][0]["url"] == "https://fastapi.tiangolo.com/"
    assert assistant["search_query"] == "FastAPI"


def test_stream_without_search_history_has_no_sources(client, web_search):
    response = client.post("/chat/stream", json={"message": "你好"})
    conversation_id = _events(response)[0]["conversation_id"]
    assistant = client.get(f"/conversations/{conversation_id}/messages").json()[-1]
    assert assistant["sources"] == []
    assert assistant["search_query"] is None


def test_stream_multi_entity_split_and_merge(client, ai_client, web_search):
    # 比较类问题：模型按实体输出多条查询词，逐实体搜索再按 URL 去重合并
    ai_client.chat.return_value = ("deepseek harness|workbuddy", TokenUsage())
    shared = SearchResult(title="两者对比", url="https://example.com/compare", snippet="对比")
    per_entity = {
        "deepseek harness": [
            SearchResult(title="Harness 官网", url="https://www.deepseek.com/harness/", snippet="dsh"),
            shared,
        ],
        "workbuddy": [
            SearchResult(title="WorkBuddy 官网", url="https://www.workbuddy.cn/", snippet="办公"),
            shared,
        ],
    }
    web_search.search.side_effect = lambda q: per_entity[q]
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    assert response.status_code == 200
    events = _events(response)
    assert events[2]["type"] == "search_result"
    assert events[2]["query"] == "deepseek harness / workbuddy"
    # 每个实体各搜一次
    assert web_search.search.call_args_list == [call("deepseek harness"), call("workbuddy")]
    urls = [r["url"] for r in events[2]["results"]]
    # 两实体官网都在，共享 URL 去重后只出现一次
    assert "https://www.deepseek.com/harness/" in urls
    assert "https://www.workbuddy.cn/" in urls
    assert urls.count("https://example.com/compare") == 1
    # 两个实体的来源都注入提示词
    prompt = ai_client.chat_stream.call_args.kwargs["system_prompt"]
    assert "https://www.deepseek.com/harness/" in prompt
    assert "https://www.workbuddy.cn/" in prompt
    # 合并来源与拼接搜索词随助手消息落库，切换会话后仍可见
    conversation_id = events[0]["conversation_id"]
    assistant = client.get(f"/conversations/{conversation_id}/messages").json()[-1]
    assert assistant["search_query"] == "deepseek harness / workbuddy"
    assert len(assistant["sources"]) == 3


def test_stream_multi_entity_fallback_heuristic_split(client, ai_client, web_search):
    # 改写失败时回退启发式：比较句仍按连接词拆成两个实体分别搜索
    ai_client.chat.side_effect = RuntimeError("model down")
    per_entity = {
        "deepseek harness": [SearchResult(title="Harness", url="https://h/", snippet="")],
        "workbuddy": [SearchResult(title="WorkBuddy", url="https://w/", snippet="")],
    }
    web_search.search.side_effect = lambda q: per_entity[q]
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    events = _events(response)
    assert events[2]["query"] == "deepseek harness / workbuddy"
    assert web_search.search.call_args_list == [call("deepseek harness"), call("workbuddy")]
    assert [r["url"] for r in events[2]["results"]] == ["https://h/", "https://w/"]
    assert events[-1]["type"] == "done"


def test_stream_multi_entity_partial_failure_keeps_survivors(client, ai_client, web_search):
    # 一个实体搜索失败不拖垮另一个：仍返回存活实体的来源
    ai_client.chat.return_value = ("deepseek harness|workbuddy", TokenUsage())

    def fake_search(query):
        if query == "deepseek harness":
            raise WebSearchError("联网搜索暂时不可用")
        return [SearchResult(title="WorkBuddy", url="https://w/", snippet="办公")]

    web_search.search.side_effect = fake_search
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    events = _events(response)
    assert events[2]["type"] == "search_result"
    assert events[2]["query"] == "deepseek harness / workbuddy"
    assert [r["url"] for r in events[2]["results"]] == ["https://w/"]
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("deepseek harness和workbuddy什么区别", ["deepseek harness", "workbuddy"]),
        ("FastAPI 与 Django 的区别", ["FastAPI", "Django"]),
        ("A vs B", ["A", "B"]),
        ("Python、Java、Go 对比", ["Python", "Java", "Go"]),
        ("deepseek harness", ["deepseek harness"]),
    ],
)
def test_split_search_entities(query, expected):
    assert split_search_entities(query) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deepseek harness|workbuddy", ["deepseek harness", "workbuddy"]),
        ('"deepseek harness" | "workbuddy"', ["deepseek harness", "workbuddy"]),
        ("deepseek harness|deepseek harness", ["deepseek harness"]),
        ("a|b|c|d", ["a", "b", "c"]),
        ("deepseek harness", ["deepseek harness"]),
        ("   ", []),
    ],
)
def test_parse_extracted_queries(raw, expected):
    assert _parse_extracted_queries(raw) == expected


def test_stream_entity_noise_serp_retries_with_exact_match(client, ai_client, web_search):
    # Bing 偶发返回无关门户页（京东）；精确匹配重试拿到正确结果后替换噪声
    ai_client.chat.return_value = ("deepseek harness|workbuddy", TokenUsage())
    noise = [SearchResult(title="京东(JD.COM)-正品低价", url="https://www.jd.com/", snippet="购物")]
    calls = []

    def fake_search(query):
        calls.append(query)
        if query == "deepseek harness":
            return [SearchResult(title="Harness 官方", url="https://h/", snippet="")]
        if query == "workbuddy":
            return noise
        if query == '"workbuddy"':
            return [SearchResult(title="WorkBuddy 官网", url="https://w/", snippet="办公")]
        return []

    web_search.search.side_effect = fake_search
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    events = _events(response)
    assert events[2]["type"] == "search_result"
    assert calls == ["deepseek harness", "workbuddy", '"workbuddy"']
    assert [r["url"] for r in events[2]["results"]] == ["https://h/", "https://w/"]


def test_stream_entity_persistent_noise_dropped(client, ai_client, web_search):
    # 重试仍无关则丢弃该实体，只保留存活实体的来源，不注入噪声
    ai_client.chat.return_value = ("deepseek harness|workbuddy", TokenUsage())
    noise = [SearchResult(title="京东", url="https://www.jd.com/", snippet="")]

    def fake_search(query):
        if query == "deepseek harness":
            return [SearchResult(title="Harness", url="https://h/", snippet="")]
        return noise

    web_search.search.side_effect = fake_search
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    events = _events(response)
    assert events[2]["type"] == "search_result"
    assert [r["url"] for r in events[2]["results"]] == ["https://h/"]
    assert events[-1]["type"] == "done"


def test_stream_all_entities_noise_reports_error(client, ai_client, web_search):
    ai_client.chat.return_value = ("deepseek harness|workbuddy", TokenUsage())
    web_search.search.return_value = [
        SearchResult(title="京东", url="https://www.jd.com/", snippet=""),
    ]
    response = client.post(
        "/chat/stream",
        json={"message": "deepseek harness和workbuddy什么区别", "web_search": True},
    )
    events = _events(response)
    assert events[2]["type"] == "search_error"
    assert events[2]["message"] == "未找到相关搜索结果"
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize(
    ("query", "titles", "expected"),
    [
        ("workbuddy", ["WorkBuddy 官网"], True),
        ("workbuddy", ["京东(JD.COM)-正品低价"], False),
        ("workbuddy", [], False),
        ("deepseek harness", ["DeepSeek Harness 教程"], True),
        ("deepseek harness", ["京东", "ChatGPT - 知乎"], False),
        ("办公助手", ["办公助手下载"], True),
    ],
)
def test_results_mention_query(query, titles, expected):
    results = [SearchResult(title=t, url="https://x/", snippet="") for t in titles]
    assert results_mention_query(query, results) is expected


def test_stream_refine_adds_second_round_and_merges(client, ai_client, web_search):
    # 审查认为不足→补搜一轮：第二轮 search_start/search_result，来源合并去重落库
    ai_client.chat.side_effect = [
        ("隐状态", TokenUsage()),
        ("隐状态 位置编码", TokenUsage()),
    ]
    round1 = [SearchResult(title="隐状态定义", url="https://a/", snippet="定义")]
    round2 = [SearchResult(title="位置编码", url="https://b/", snippet="编码")]
    web_search.search.side_effect = lambda q: {"隐状态": round1, "隐状态 位置编码": round2}[q]
    response = client.post(
        "/chat/stream", json={"message": "隐状态中的位置信息什么意思", "web_search": True}
    )
    events = _events(response)
    assert [e["type"] for e in events] == [
        "start", "search_start", "search_result", "search_start", "search_result",
        "delta", "delta", "done",
    ]
    assert events[3]["query"] == "隐状态 位置编码"
    assert events[4]["query"] == "隐状态 / 隐状态 位置编码"
    assert [r["url"] for r in events[4]["results"]] == ["https://a/", "https://b/"]
    assert web_search.search.call_args_list == [call("隐状态"), call("隐状态 位置编码")]
    prompt = ai_client.chat_stream.call_args.kwargs["system_prompt"]
    assert "https://a/" in prompt and "https://b/" in prompt
    conversation_id = events[0]["conversation_id"]
    assistant = client.get(f"/conversations/{conversation_id}/messages").json()[-1]
    assert assistant["search_query"] == "隐状态 / 隐状态 位置编码"
    assert len(assistant["sources"]) == 2


def test_stream_refine_enough_skips_second_round(client, ai_client, web_search):
    ai_client.chat.side_effect = [("FastAPI", TokenUsage()), ("ENOUGH", TokenUsage())]
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    events = _events(response)
    assert [e["type"] for e in events] == [
        "start", "search_start", "search_result", "delta", "delta", "done",
    ]
    web_search.search.assert_called_once_with("FastAPI")


def test_stream_refine_dedupes_first_round_queries(client, ai_client, web_search):
    # 审查回显第一轮已有查询词→去重后无新查询，不补搜
    ai_client.chat.side_effect = [("FastAPI", TokenUsage()), ("FastAPI", TokenUsage())]
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    events = _events(response)
    assert [e["type"] for e in events] == [
        "start", "search_start", "search_result", "delta", "delta", "done",
    ]
    web_search.search.assert_called_once_with("FastAPI")


def test_stream_refine_caps_queries(client, ai_client, web_search):
    ai_client.chat.side_effect = [("主题", TokenUsage()), ("a|b|c", TokenUsage())]
    web_search.search.side_effect = lambda q: [SearchResult(title=q, url=f"https://{q}/", snippet=q)]
    response = client.post("/chat/stream", json={"message": "主题", "web_search": True})
    events = _events(response)
    # 只补前两条 a、b，c 被补搜上限截断
    assert web_search.search.call_args_list == [call("主题"), call("a"), call("b")]
    assert events[4]["query"] == "主题 / a / b"


def test_stream_refine_noise_round2_keeps_round1(client, ai_client, web_search):
    ai_client.chat.side_effect = [("隐状态", TokenUsage()), ("补充角度", TokenUsage())]
    round1 = [SearchResult(title="隐状态定义", url="https://a/", snippet="定义")]
    noise = [SearchResult(title="京东", url="https://jd/", snippet="购物")]
    web_search.search.side_effect = lambda q: round1 if q == "隐状态" else noise
    response = client.post("/chat/stream", json={"message": "隐状态", "web_search": True})
    events = _events(response)
    # 补搜轮全噪声被门控丢弃→保留第一轮来源，SSE 仍收尾不卡在 searching
    assert events[4]["type"] == "search_result"
    assert [r["url"] for r in events[4]["results"]] == ["https://a/"]
    assert events[-1]["type"] == "done"


def test_stream_refine_failure_skips(client, ai_client, web_search):
    ai_client.chat.side_effect = [("FastAPI", TokenUsage()), RuntimeError("review down")]
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    events = _events(response)
    assert [e["type"] for e in events] == [
        "start", "search_start", "search_result", "delta", "delta", "done",
    ]
    web_search.search.assert_called_once_with("FastAPI")


def test_stream_refine_usage_counted(client, ai_client, web_search):
    # 改写15 + 审查15 + 回复15 = 45（短会话无摘要用量）
    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    ai_client.chat.side_effect = [("FastAPI", usage), ("ENOUGH", usage)]
    response = client.post("/chat/stream", json={"message": "FastAPI 是什么", "web_search": True})
    events = _events(response)
    assert events[-1]["usage"]["total_tokens"] == 45


def test_chat_non_stream_refine_merges(client, ai_client, web_search):
    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    ai_client.chat.side_effect = [
        ("隐状态", usage),
        ("隐状态 位置编码", usage),
        ("测试回复", usage),
    ]
    round1 = [SearchResult(title="隐状态定义", url="https://a/", snippet="定义")]
    round2 = [SearchResult(title="位置编码", url="https://b/", snippet="编码")]
    web_search.search.side_effect = lambda q: {"隐状态": round1, "隐状态 位置编码": round2}[q]
    response = client.post("/chat/chat", json={"message": "隐状态", "web_search": True})
    body = response.json()
    assert body["reply"] == "测试回复"
    assert [s["url"] for s in body["sources"]] == ["https://a/", "https://b/"]
    assert body["usage"]["total_tokens"] == 45