import argparse
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from core.paths import BASE_DIR, SYSTEM_PROMPT_FILE
from db.database import Database
from db.orm_models import Conversation
from models.schemas import Message
from services.ai_client import AIClient
from services.context_manager import (
    ContextManager,
    estimate_request_tokens,
    estimate_tokens,
)
from services.memory_store import MemoryStore

DEFAULTS = {
    "context_max_tokens": 16384,
    "context_response_reserve": 4096,
    "context_summary_max_tokens": 1024,
    "context_recent_turns": 6,
}
TOPICS = [
    "登录表单校验",
    "接口分页",
    "会话列表排序",
    "异常提示",
    "服务健康检查",
    "消息流式展示",
    "数据库索引",
    "限流窗口",
    "重复提交",
    "来源链接展示",
    "接口超时",
    "日志脱敏",
    "会话标题",
    "移动端布局",
    "请求取消",
    "事务边界",
    "消息排序",
    "用户隔离",
    "删除会话",
    "缓存失效",
    "前端状态同步",
    "测试数据隔离",
    "依赖注入",
    "发布前检查",
]
FACTS = {
    1: "已确认项目叫灯塔客服，后端使用 FastAPI。",
    2: "已确认正式数据库是 PostgreSQL 18；安全红线是不存明文密码、不记录 API 密钥。",
    3: "最初确定每月预算为500元。",
    4: "最初确定上线日期为2026-12-15。",
    8: "明确更正：月预算改为300元，之前的500元作废。",
    12: "明确更正：上线日期改为2026-12-20，之前的2026-12-15作废。",
    14: "注册登录功能已完成验收。唯一确认的未完成事项是SSE断线重连测试；其他讨论只是建议，不算待办。",
    26: "再次更正：月预算现在为200元，取代之前的300元。",
    27: "再次更正：最新上线日期为2026-12-28，取代之前的2026-12-20。",
    29: "SSE断线重连测试已完成。唯一确认的未完成事项改为搜索来源引用准确性回归。",
}
QUESTION = (
    "请根据我们已经确认的最新决定回顾项目，不要把建议当作已确认事项。"
    "只输出一个JSON对象，不要Markdown围栏，字段为：project（项目名字符串）、"
    "backend（后端框架字符串）、database（数据库含版本字符串）、budget_yuan（最新月预算整数）、"
    "deadline（最新上线日期YYYY-MM-DD）、no_plaintext_password（布尔值）、"
    "no_api_key_logging（布尔值）、pending（唯一确认的未完成事项字符串）。"
)


def scenario_round(number: int) -> list[Message]:
    topic = TOPICS[(number - 1) % len(TOPICS)]
    fact = FACTS.get(number, "本轮没有新的业务决定，沿用已经确认的约束。")
    return [
        Message(
            role="user",
            content=(
                f"第{number}轮：我们讨论{topic}。{fact}"
                "请说明实施步骤、边界情况和验收标准，其余部分先讨论方案，不改变已有业务约束。"
            ),
        ),
        Message(
            role="assistant",
            content=(
                f"关于{topic}，建议先梳理输入输出与调用链，再补充正常、错误和并发路径的验收用例。"
                "实现时保持各模块责任清晰，状态变更放入同一事务，日志记录可定位问题的标识而不是敏感正文。"
                "交付前检查重复请求、超时、权限不足及刷新后的状态一致性，使用独立测试数据。"
                "最后通过回归用例确认已有功能没有退化；这些是技术建议，不新增项目待办或改变已确认的决定。"
            ),
        ),
    ]


class MeasuredClient:
    def __init__(self, client: AIClient):
        self.client = client
        self.calls: list[dict] = []
        self.phase = ""
        self.operation = "summary"

    def chat(self, *, system_prompt, messages, max_output_tokens):
        if len(self.calls) >= 8:
            raise RuntimeError("Reached the eight-call limit")
        call = {
            "phase": self.phase,
            "operation": self.operation,
            "input_estimate": estimate_request_tokens(system_prompt, messages),
            "output_limit": max_output_tokens,
        }
        assert call["input_estimate"] <= 12288
        self.calls.append(call)
        started = perf_counter()
        try:
            reply, usage = self.client.chat(
                system_prompt=system_prompt,
                messages=messages,
                max_output_tokens=max_output_tokens,
            )
            call["usage"] = usage.model_dump()
            call["reply"] = reply
            return reply, usage
        finally:
            call["seconds"] = round(perf_counter() - started, 2)


def check_facts(
    reply: str, budget: int, deadline: str, pending: str
) -> dict[str, bool]:
    try:
        answer = json.loads(reply)
    except json.JSONDecodeError:
        return {"valid_json": False}
    if not isinstance(answer, dict):
        return {"valid_json": False}
    database = str(answer.get("database", "")).lower().replace(" ", "")
    return {
        "project": answer.get("project") == "灯塔客服",
        "backend": "fastapi" in str(answer.get("backend", "")).lower(),
        "database": "postgresql" in database and "18" in database,
        "budget_yuan": answer.get("budget_yuan") == budget,
        "deadline": answer.get("deadline") == deadline,
        "no_plaintext_password": answer.get("no_plaintext_password") is True,
        "no_api_key_logging": answer.get("no_api_key_logging") is True,
        "pending": pending in str(answer.get("pending", "")),
    }


def run_phase(
    database, client, system_prompt, *, name, first, last, budget, deadline, pending
):
    client.phase = name
    client.operation = "summary"
    call_start = len(client.calls)
    with database.session() as session:
        store = MemoryStore(session)
        for number in range(first, last + 1):
            for message in scenario_round(number):
                store.add_message("synthetic-long-chat", message)
        store.add_message("synthetic-long-chat", Message(role="user", content=QUESTION))
        history = store.get_messages("synthetic-long-chat")
        old_summary, previous_records = store.get_context("synthetic-long-chat")
        pending_messages = [
            Message(role=row.role, content=row.content) for row in previous_records
        ]
        previous_context = ContextManager._with_summary(old_summary, pending_messages)
        before = estimate_request_tokens(system_prompt, previous_context)
        assert before > 12288, "Scenario must trigger compaction at the default budget"

        context, summary_usage = ContextManager(store).build(
            "synthetic-long-chat", client, system_prompt
        )
        after = estimate_request_tokens(system_prompt, context)
        summary, remaining = store.get_context("synthetic-long-chat")
        through_id = session.get(Conversation, "synthetic-long-chat").summary_through_id
        assert after <= 12288
        assert len(context) == 14
        assert context[1:] == history[-13:]
        assert store.get_messages("synthetic-long-chat") == history
        assert summary and estimate_tokens(summary) <= 1024
        assert remaining[0].content.startswith(f"第{last - 5}轮")
        assert str(budget) not in "".join(message.content for message in context[1:])
        assert deadline not in "".join(message.content for message in context[1:])
        calls_after_compaction = len(client.calls)
        reused, reuse_usage = ContextManager(store).build(
            "synthetic-long-chat", client, system_prompt
        )
        assert reused == context and reuse_usage.total_tokens == 0
        assert len(client.calls) == calls_after_compaction

        result = {
            "phase": name,
            "history_message_count_before_reply": len(history),
            "input_estimate_before": before,
            "input_estimate_after": after,
            "input_reduction_percent": round((1 - after / before) * 100, 1),
            "recent_complete_turns": 6,
            "recent_round_range": [last - 5, last],
            "context_message_count": len(context),
            "summary_calls": calls_after_compaction - call_start,
            "summary_bytes": estimate_tokens(summary),
            "summary": summary,
            "summary_through_id": through_id,
            "summary_reused_without_call": True,
            "history_unchanged_by_compaction": True,
            "summary_usage": summary_usage.model_dump(),
        }
        client.operation = "recall"
        reply, reply_usage = client.chat(
            system_prompt=system_prompt,
            messages=context,
            max_output_tokens=1024,
        )
        result["recall_reply"] = reply
        result["fact_checks"] = check_facts(reply, budget, deadline, pending)
        store.add_message(
            "synthetic-long-chat", Message(role="assistant", content=reply)
        )
        store.add_token_usage("synthetic-long-chat", summary_usage)
        store.add_token_usage("synthetic-long-chat", reply_usage)
        session.commit()

    with database.session() as session:
        store = MemoryStore(session)
        assert store.get_context("synthetic-long-chat")[0] == summary
        assert len(store.get_messages("synthetic-long-chat")) == len(history) + 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="允许调用真实模型，最多8次")
    args = parser.parse_args()
    for key, value in DEFAULTS.items():
        setattr(settings, key, value)
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    first_history = [
        message for number in range(1, 25) for message in scenario_round(number)
    ]
    first_input = estimate_request_tokens(
        system_prompt, first_history + [Message(role="user", content=QUESTION)]
    )
    if not args.live:
        print(
            json.dumps(
                {
                    "mode": "preview_only_no_model_calls",
                    "phase1_complete_rounds": 24,
                    "phase1_input_estimate": first_input,
                    "phase1_recent_window_estimate": estimate_request_tokens(
                        system_prompt,
                        ContextManager._with_summary(
                            "x" * 1024,
                            first_history[-12:]
                            + [Message(role="user", content=QUESTION)],
                        ),
                    ),
                    "input_budget": 12288,
                    "phase2_added_rounds": 17,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    ai_client = AIClient()
    ai_client.strategy.client = ai_client.strategy.client.with_options(max_retries=0)
    client = MeasuredClient(ai_client)
    temp_root = BASE_DIR / ".test-tmp"
    temp_root.mkdir(exist_ok=True)
    output = temp_root / "context-compression-result.json"
    report = {
        "provider": settings.ai_provider,
        "model": ai_client.strategy.model,
        "settings": DEFAULTS,
        "metric": "UTF-8 byte-based conservative estimate, not provider token count",
        "calls": client.calls,
        "phases": [],
        "success": False,
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="context-scenario-", dir=temp_root
        ) as directory:
            database = Database(Path(directory) / "scenario.db")
            database.engine.echo = False
            try:
                database.init_tables()
                for phase in [
                    dict(
                        name="first_compaction",
                        first=1,
                        last=24,
                        budget=300,
                        deadline="2026-12-20",
                        pending="断线重连",
                    ),
                    dict(
                        name="rolling_update",
                        first=26,
                        last=42,
                        budget=200,
                        deadline="2026-12-28",
                        pending="来源引用准确性",
                    ),
                ]:
                    report["phases"].append(
                        run_phase(database, client, system_prompt, **phase)
                    )
                with database.session() as session:
                    store = MemoryStore(session)
                    report["persisted_message_count"] = len(
                        store.get_messages("synthetic-long-chat")
                    )
                    report["total_usage"] = store.list_conversations()[
                        0
                    ].usage.model_dump()
                checks = [
                    passed
                    for phase in report["phases"]
                    for passed in phase["fact_checks"].values()
                ]
                report["all_structural_checks_passed"] = True
                report["fact_check_score"] = {
                    "passed": sum(checks),
                    "total": len(checks),
                }
                report["success"] = all(checks)
            finally:
                database.engine.dispose()
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, (AssertionError, RuntimeError)):
            report["error"] = str(exc)
    finally:
        ai_client.strategy.client.close()
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Report: {output}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
