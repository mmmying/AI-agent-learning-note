import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from core.config import Settings, settings
from db.orm_models import Conversation
from models.schemas import Message, TokenUsage
from services.ai_client import AIClient
from services.context_manager import ContextManager, estimate_request_tokens, estimate_tokens
from services.memory_store import MemoryStore
from services.strategy import OpenAICompatibleStrategy

pytestmark = pytest.mark.database


@pytest.fixture
def small_context(monkeypatch):
    monkeypatch.setattr(settings, "context_max_tokens", 4096)
    monkeypatch.setattr(settings, "context_response_reserve", 1024)
    monkeypatch.setattr(settings, "context_summary_max_tokens", 256)
    monkeypatch.setattr(settings, "context_recent_turns", 1)


def seed_rounds(store, conversation_id="long-chat", prefix="old", count=3):
    for index in range(count):
        store.add_message(conversation_id, Message(role="user", content=f"{prefix}-question-{index}:" + "a" * 600))
        store.add_message(conversation_id, Message(role="assistant", content=f"{prefix}-answer-{index}:" + "b" * 450))


def summarizer():
    client = MagicMock(spec=AIClient)
    client.chat.return_value = (
        "目标：学习 Python；约束：不引入新框架；待办：测试接口。",
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    return client


def test_short_history_uses_no_summary_even_with_many_turns(memory_store, small_context):
    for index in range(10):
        memory_store.add_message("short", Message(role="user", content=str(index)))
        memory_store.add_message("short", Message(role="assistant", content="好"))
    memory_store.add_message("short", Message(role="user", content="继续"))
    ai = summarizer()
    messages, usage = ContextManager(memory_store).build("short", ai, "system")
    assert messages == memory_store.get_messages("short")
    assert usage.total_tokens == 0
    ai.chat.assert_not_called()


def test_rolls_summary_keeps_recent_complete_turn_and_reuses(memory_store, small_context):
    seed_rounds(memory_store)
    memory_store.add_message("long-chat", Message(role="user", content="当前问题"))
    original = memory_store.get_messages("long-chat")
    ai = summarizer()
    manager = ContextManager(memory_store)
    messages, usage = manager.build("long-chat", ai, "system")
    assert messages[0].role == "assistant"
    assert "较早对话摘要" in messages[0].content
    assert messages[1:] == original[-3:]
    assert [m.role for m in messages[1:]] == ["user", "assistant", "user"]
    assert memory_store.get_messages("long-chat") == original
    assert usage.total_tokens == ai.chat.call_count * 15
    assert estimate_request_tokens("system", messages) <= 3072
    summary, remaining = memory_store.get_context("long-chat")
    assert estimate_tokens(summary) <= 256
    assert [row.content for row in remaining] == [m.content for m in original[-3:]]
    ai.chat.reset_mock()
    again, usage = manager.build("long-chat", ai, "system")
    assert again == messages
    assert usage.total_tokens == 0
    ai.chat.assert_not_called()


def test_next_compaction_only_summarizes_new_history(memory_store, small_context):
    seed_rounds(memory_store)
    memory_store.add_message("long-chat", Message(role="user", content="当前问题"))
    ai = summarizer()
    manager = ContextManager(memory_store)
    manager.build("long-chat", ai, "system")
    old_summary, _ = memory_store.get_context("long-chat")
    memory_store.add_message("long-chat", Message(role="assistant", content="当前回复"))
    seed_rounds(memory_store, prefix="new")
    memory_store.add_message("long-chat", Message(role="user", content="下一问"))
    ai.chat.reset_mock()
    messages, _ = manager.build("long-chat", ai, "system")
    prompts = [call.kwargs["messages"][0].content for call in ai.chat.call_args_list]
    assert old_summary in prompts[0]
    assert "old-question-0" not in "".join(prompts)
    assert "new-question-0" in "".join(prompts)
    assert messages[-1].content == "下一问"
    assert len(memory_store.get_messages("long-chat")) == 15


def test_huge_old_round_is_summarized_in_bounded_batches(memory_store, small_context):
    memory_store.add_message("huge", Message(role="user", content="中🙂" * 2000))
    memory_store.add_message("huge", Message(role="assistant", content="旧答复"))
    memory_store.add_message("huge", Message(role="user", content="现在的问题"))
    ai = summarizer()
    messages, usage = ContextManager(memory_store).build("huge", ai, "system")
    assert ai.chat.call_count > 1
    assert usage.total_tokens == ai.chat.call_count * 15
    chunks = []
    for call in ai.chat.call_args_list:
        assert estimate_request_tokens(call.kwargs["system_prompt"], call.kwargs["messages"]) <= 3072
        assert call.kwargs["max_output_tokens"] == 256
        chunks.append(call.kwargs["messages"][0].content.split("新增历史片段：\n", 1)[1])
    assert json.loads("".join(chunks)) == [
        {"role": "user", "content": "中🙂" * 2000},
        {"role": "assistant", "content": "旧答复"},
    ]
    assert [m.content for m in messages[1:]] == ["现在的问题"]
    assert len(memory_store.get_messages("huge")) == 3


def test_generated_summary_is_bounded(memory_store, small_context):
    seed_rounds(memory_store)
    memory_store.add_message("long-chat", Message(role="user", content="当前问题"))
    ai = summarizer()
    ai.chat.return_value = ("中文🙂" * 1000, TokenUsage())
    messages, _ = ContextManager(memory_store).build("long-chat", ai, "system")
    summary, _ = memory_store.get_context("long-chat")
    assert estimate_tokens(summary) <= 256
    assert "�" not in summary
    assert estimate_request_tokens("system", messages) <= 3072


def test_large_current_message_is_not_summarized_or_dropped(memory_store, small_context):
    seed_rounds(memory_store)
    memory_store.add_message("long-chat", Message(role="user", content="x" * 4000))
    ai = summarizer()
    with pytest.raises(HTTPException) as exc:
        ContextManager(memory_store).build("long-chat", ai, "system")
    assert exc.value.status_code == 413
    ai.chat.assert_not_called()
    assert memory_store.get_context("long-chat")[0] == ""


@pytest.mark.parametrize("stream", [False, True])
def test_api_compaction_persists_history_and_counts_summary_usage(
    client, database, ai_client, small_context, stream
):
    with database.session() as session:
        seed_rounds(MemoryStore(session, client.user_id))
        session.commit()
    response = client.post("/chat/stream" if stream else "/chat/chat", json={
        "conversation_id": "long-chat", "message": "新问题",
    })
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[0]["type"] == "start"
        assert events[-1]["type"] == "done"
        usage = events[-1]["usage"]
        final_call = ai_client.chat_stream.call_args
        summary_calls = ai_client.chat.call_count
    else:
        usage = response.json()["usage"]
        final_call = ai_client.chat.call_args
        summary_calls = ai_client.chat.call_count - 1
    assert summary_calls >= 1
    assert usage["total_tokens"] == 15 * (summary_calls + 1)
    assert final_call.kwargs["max_output_tokens"] == 1024
    assert "较早对话摘要" in final_call.kwargs["messages"][0].content
    assert len(client.get("/conversations/long-chat/messages").json()) == 8
    assert client.get("/conversations").json()[0]["usage"] == usage
    with database.session() as session:
        conversation = session.get(Conversation, "long-chat")
        assert conversation.context_summary
        assert conversation.summary_through_id > 0


@pytest.mark.parametrize("endpoint", ["/chat/chat", "/chat/stream"])
def test_api_rejects_oversized_input_before_stream_or_ai(client, database, ai_client, small_context, endpoint):
    response = client.post(endpoint, json={"message": "中" * 2000})
    assert response.status_code == 413
    assert "过长" in response.json()["detail"]
    ai_client.chat.assert_not_called()
    ai_client.chat_stream.assert_not_called()
    with database.session() as session:
        assert MemoryStore(session, client.user_id).list_conversations() == []


@pytest.mark.parametrize("endpoint", ["/chat/chat", "/chat/stream"])
def test_empty_summary_rolls_back_current_round(client, database, ai_client, small_context, endpoint):
    with database.session() as session:
        seed_rounds(MemoryStore(session, client.user_id))
        session.commit()
    ai_client.chat.return_value = ("  ", TokenUsage())
    response = client.post(endpoint, json={"conversation_id": "long-chat", "message": "新问题"})
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "生成对话摘要失败" in response.json()["detail"]
    with database.session() as session:
        store = MemoryStore(session, client.user_id)
        assert len(store.get_messages("long-chat")) == 6
        assert store.get_context("long-chat")[0] == ""


@pytest.mark.parametrize("stream", [False, True])
def test_reply_failure_rolls_back_generated_summary(client, database, ai_client, small_context, stream):
    with database.session() as session:
        seed_rounds(MemoryStore(session, client.user_id))
        session.commit()

    def fail_reply(**kwargs):
        if kwargs["max_output_tokens"] == 256:
            return "历史摘要", TokenUsage(input_tokens=10, total_tokens=10)
        raise RuntimeError("reply failed")

    ai_client.chat.side_effect = fail_reply
    ai_client.chat_stream.side_effect = RuntimeError("reply failed")
    with pytest.raises(RuntimeError, match="reply failed"):
        client.post("/chat/stream" if stream else "/chat/chat", json={
            "conversation_id": "long-chat", "message": "新问题",
        })
    with database.session() as session:
        store = MemoryStore(session, client.user_id)
        assert len(store.get_messages("long-chat")) == 6
        assert store.get_context("long-chat")[0] == ""
        assert session.scalar(select(Conversation.total_tokens)) == 0


def test_search_prompt_is_included_in_budget(client, database, ai_client, web_search, small_context):
    with database.session() as session:
        seed_rounds(MemoryStore(session, client.user_id))
        session.commit()
    ai_client.chat.side_effect = lambda **kwargs: (
        "FastAPI" if "搜索词提取器" in kwargs["system_prompt"] else "测试回复",
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    response = client.post("/chat/chat", json={
        "conversation_id": "long-chat", "message": "FastAPI", "web_search": True,
    })
    assert response.status_code == 200
    call = ai_client.chat.call_args.kwargs
    assert "联网搜索参考" in call["system_prompt"]
    assert "较早对话摘要" in call["messages"][0].content
    assert estimate_request_tokens(call["system_prompt"], call["messages"]) <= 3072


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("completion_type", ["response.completed", "response.incomplete"])
def test_ai_client_forwards_output_limit_to_provider(monkeypatch, stream, completion_type):
    sdk = MagicMock()
    raw_usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
    strategy = OpenAICompatibleStrategy(sdk, "test-model")
    monkeypatch.setattr("services.ai_client.AIProviderFactory.create", lambda **kwargs: strategy)
    client = AIClient()
    if stream:
        sdk.responses.create.return_value = iter([
            SimpleNamespace(type="response.output_text.delta", delta="reply"),
            SimpleNamespace(type=completion_type, response=SimpleNamespace(usage=raw_usage)),
        ])
        result = client.chat_stream("system", [Message(role="user", content="hello")], max_output_tokens=123)
        assert next(result) == "reply"
        with pytest.raises(StopIteration) as stop:
            next(result)
        assert stop.value.value.total_tokens == 15
    else:
        sdk.responses.create.return_value = SimpleNamespace(output_text="reply", usage=raw_usage)
        assert client.chat("system", [Message(role="user", content="hello")], max_output_tokens=123)[0] == "reply"
    assert sdk.responses.create.call_args.kwargs["max_output_tokens"] == 123


def test_zero_recent_turns_keeps_current_question(memory_store, small_context, monkeypatch):
    monkeypatch.setattr(settings, "context_recent_turns", 0)
    seed_rounds(memory_store)
    memory_store.add_message("long-chat", Message(role="user", content="当前问题"))
    messages, _ = ContextManager(memory_store).build("long-chat", summarizer(), "system")
    assert len(messages) == 2
    assert messages[-1].content == "当前问题"
    assert len(memory_store.get_messages("long-chat")) == 7


def test_exact_input_budget_does_not_summarize(memory_store, small_context):
    content_budget = 3072 - estimate_request_tokens("system", [Message(role="user", content="")])
    memory_store.add_message("exact", Message(role="user", content="a" * content_budget))
    ai = summarizer()
    messages, _ = ContextManager(memory_store).build("exact", ai, "system")
    assert estimate_request_tokens("system", messages) == 3072
    ai.chat.assert_not_called()


@pytest.mark.parametrize("endpoint", ["/chat/chat", "/chat/stream"])
def test_search_overflow_returns_error_before_start(client, database, ai_client, small_context, endpoint, monkeypatch):
    monkeypatch.setattr(
        "services.chat_service.ChatService._search_context",
        lambda self, request, queries: (self.system_prompt + "s" * 1000, [], None),
    )
    response = client.post(endpoint, json={"message": "FastAPI " + "x" * 2650, "web_search": True})
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/json")
    ai_client.chat_stream.assert_not_called()
    ai_client.chat.assert_not_called()
    with database.session() as session:
        assert MemoryStore(session, client.user_id).list_conversations() == []


@pytest.mark.parametrize("stream", [False, True])
def test_search_and_summary_usage_are_both_counted(client, database, ai_client, web_search, small_context, stream):
    with database.session() as session:
        seed_rounds(MemoryStore(session, client.user_id))
        session.commit()
    ai_client.chat.side_effect = lambda **kwargs: (
        "FastAPI" if "搜索词提取器" in kwargs["system_prompt"] else "历史摘要",
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    response = client.post("/chat/stream" if stream else "/chat/chat", json={
        "conversation_id": "long-chat", "message": "FastAPI", "web_search": True,
    })
    assert response.status_code == 200
    assert client.get("/conversations").json()[0]["usage"]["total_tokens"] == (
        ai_client.chat.call_count + int(stream)
    ) * 15
    for call in ai_client.chat.call_args_list:
        kwargs = call.kwargs
        assert estimate_request_tokens(kwargs["system_prompt"], kwargs["messages"]) <= 3072
        assert kwargs["max_output_tokens"] <= 1024


def test_long_conversation_scenario_harness_offline(database, ai_client, monkeypatch):
    from core.paths import SYSTEM_PROMPT_FILE
    from scripts.test_context_compression import DEFAULTS, MeasuredClient, run_phase

    for name, value in DEFAULTS.items():
        monkeypatch.setattr(settings, name, value)
    measured = MeasuredClient(ai_client)

    def fake_model(**kwargs):
        second = measured.phase == "rolling_update"
        facts = {
            "project": "灯塔客服", "backend": "FastAPI", "database": "PostgreSQL 18",
            "budget_yuan": 200 if second else 300,
            "deadline": "2026-12-28" if second else "2026-12-20",
            "no_plaintext_password": True, "no_api_key_logging": True,
            "pending": "搜索来源引用准确性回归" if second else "SSE断线重连测试",
        }
        return json.dumps(facts, ensure_ascii=False), TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)

    ai_client.chat.side_effect = fake_model
    prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    first = run_phase(
        database, measured, prompt, name="first_compaction", first=1, last=24,
        budget=300, deadline="2026-12-20", pending="断线重连",
    )
    second = run_phase(
        database, measured, prompt, name="rolling_update", first=26, last=42,
        budget=200, deadline="2026-12-28", pending="来源引用准确性",
    )
    assert first["recent_round_range"] == [19, 24]
    assert second["recent_round_range"] == [37, 42]
    assert first["summary_through_id"] < second["summary_through_id"]
    assert first["input_reduction_percent"] > 50
    assert second["input_reduction_percent"] > 50
    assert all(first["fact_checks"].values()) and all(second["fact_checks"].values())
    with database.session() as session:
        store = MemoryStore(session)
        assert len(store.get_messages("synthetic-long-chat")) == 86
        assert store.list_conversations()[0].usage.total_tokens == 15 * len(measured.calls)


def test_scenario_fact_check_flags_project_name_drift():
    from scripts.test_context_compression import check_facts

    reply = json.dumps({
        "project": "项目灯塔客服", "backend": "FastAPI", "database": "PostgreSQL 18",
        "budget_yuan": 200, "deadline": "2026-12-28",
        "no_plaintext_password": True, "no_api_key_logging": True,
        "pending": "搜索来源引用准确性回归",
    }, ensure_ascii=False)
    checks = check_facts(reply, 200, "2026-12-28", "来源引用准确性")
    assert checks["project"] is False
    assert sum(checks.values()) == 7


def test_scenario_model_call_limit_is_enforced(ai_client):
    from scripts.test_context_compression import MeasuredClient

    measured = MeasuredClient(ai_client)
    for _ in range(8):
        measured.chat(system_prompt="system", messages=[], max_output_tokens=1024)
    with pytest.raises(RuntimeError, match="eight-call limit"):
        measured.chat(system_prompt="system", messages=[], max_output_tokens=1024)
    assert ai_client.chat.call_count == 8


@pytest.mark.parametrize("values", [
    {"context_recent_turns": -1},
    {"context_max_tokens": 4096, "context_response_reserve": 4096},
    {"context_summary_max_tokens": 2048, "context_response_reserve": 1024},
])
def test_invalid_budget_settings(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
