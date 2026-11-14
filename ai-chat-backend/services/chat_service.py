import json
import logging
from collections.abc import Generator

from core.config import settings
from core.paths import SYSTEM_PROMPT_FILE
from models.schemas import ChatRequest, ChatResponse, Message, SearchResult, TokenUsage
from services.ai_client import AIClient
from services.context_manager import ContextManager
from services.memory_store import MemoryStore
from services.web_search import (
    WebSearchError,
    WebSearchService,
    build_search_query,
    results_mention_query,
    split_search_entities,
)

logger = logging.getLogger(__name__)

# 单条摘要注入提示词时的长度上限，避免个别长摘要撑爆上下文
_SNIPPET_LIMIT = 400
# 比较类消息最多拆出的实体数，避免搜索次数与提示词膨胀
_MAX_ENTITIES = 3

# 词表剥离覆盖不了任意口语（如“我不懂deepseek harness”），改由模型把消息改写成搜索词。
# 多实体/比较类消息让模型按实体输出多条查询词（| 分隔），便于拆分搜索再合并。
_QUERY_EXTRACT_PROMPT = (
    "你是搜索词提取器。把用户消息改写成简洁的搜索引擎查询词，"
    "只保留要查询的主题或实体，去掉口语、态度、祈使等无关外壳。"
    "若消息涉及多个实体或比较（如 A 和 B 的区别），为每个实体各输出一条查询词，用 | 分隔；"
    "否则只输出一条查询词。不要解释、不要引号、不要句末标点。"
    "若消息已是清晰的主题词，原样输出。"
)

# 补搜一轮的查询上限：审查后最多再补两条，控制额外搜索与提示词膨胀
_MAX_REFINE_QUERIES = 2

# 第一轮搜索后让模型审查结果充分性：足够输出 ENOUGH，不足输出补充查询词。
# 这是流水线里唯一“看完结果再决定”的环节，最多触发一轮补搜。
_REVIEW_SEARCH_PROMPT = (
    "你是搜索充分性审查器。给定用户问题和第一轮搜索结果的标题与摘要，"
    "判断这些结果是否足以回答用户问题。"
    "若足够，只输出 ENOUGH。"
    "若不足（缺少关键实体、缺少最新信息、结果与问题无关），"
    f"输出最多 {_MAX_REFINE_QUERIES} 条补充搜索词，用 | 分隔，聚焦缺失的角度；"
    "不要解释、不要引号、不要句末标点。"
)


def _clean_extracted_query(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = line.strip("\"'“”‘’「」『』。．.！!？?，,；;：:")
    return " ".join(line.split())[:100]


def _parse_extracted_queries(text: str) -> list[str]:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    queries: list[str] = []
    for segment in line.split("|"):
        query = _clean_extracted_query(segment)
        if query and query not in queries:
            queries.append(query)
    return queries[:_MAX_ENTITIES]


class ChatService:
    # 初始化建立存储依赖和系统提示词
    def __init__(self, memory_store: MemoryStore) -> None:
        self.memory_store = memory_store
        self.system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
        self.web_search_service = WebSearchService()
        self.context_manager = ContextManager(memory_store)

    def validate_message(self, message: str) -> None:
        self.context_manager.validate_message(self.system_prompt, message)

    def chat(self, request: ChatRequest) -> ChatResponse:
        ai_client = AIClient(provider=request.provider)
        conversation_id = (
            request.conversation_id or self.memory_store.create_conversation_id()
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="user", content=request.message),
        )

        queries, query_usage = (
            self._extract_search_queries(ai_client, request.message)
            if request.web_search
            else (None, TokenUsage())
        )
        system_prompt, sources, search_query, _, refine_usage = self._search_with_refine(
            request, ai_client, queries
        )
        messages, summary_usage = self.context_manager.build(
            conversation_id, ai_client, system_prompt
        )

        reply, reply_usage = ai_client.chat(
            system_prompt=system_prompt,
            messages=messages,
            max_output_tokens=settings.context_response_reserve,
        )
        usage = TokenUsage(
            input_tokens=(
                summary_usage.input_tokens
                + query_usage.input_tokens
                + refine_usage.input_tokens
                + reply_usage.input_tokens
            ),
            output_tokens=(
                summary_usage.output_tokens
                + query_usage.output_tokens
                + refine_usage.output_tokens
                + reply_usage.output_tokens
            ),
            total_tokens=(
                summary_usage.total_tokens
                + query_usage.total_tokens
                + refine_usage.total_tokens
                + reply_usage.total_tokens
            ),
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="assistant", content=reply, sources=sources, search_query=search_query),
        )
        self.memory_store.add_token_usage(conversation_id, usage)

        # 一轮对话作为一个事务，持久化成功后再返回回复。
        self.memory_store.session.commit()
        return ChatResponse(
            conversation_id=conversation_id,
            reply=reply,
            usage=usage,
            sources=sources,
        )

    def chat_stream(self, request: ChatRequest) -> Generator[str, None, None]:
        # 流式对话，以 SSE 格式逐段产出回复，结束后把完整回复存入记忆
        ai_client = AIClient(provider=request.provider)
        conversation_id = (
            request.conversation_id or self.memory_store.create_conversation_id()
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="user", content=request.message),
        )


        search_events: list[dict] = []
        if request.web_search:
            search_events.append(
                {"type": "search_start", "query": build_search_query(request.message)}
            )
            queries, query_usage = self._extract_search_queries(ai_client, request.message)
        else:
            queries = None
            query_usage = TokenUsage()
        system_prompt, sources, search_query, _, refine_usage = self._search_with_refine(
            request, ai_client, queries, events=search_events if request.web_search else None
        )

        messages, usage = self.context_manager.build(
            conversation_id, ai_client, system_prompt
        )
        usage.input_tokens += query_usage.input_tokens + refine_usage.input_tokens
        usage.output_tokens += query_usage.output_tokens + refine_usage.output_tokens
        usage.total_tokens += query_usage.total_tokens + refine_usage.total_tokens
        # 在响应头发出前完成摘要，准备失败仍能返回 HTTP 错误而不泄露临时会话 ID。
        def generate() -> Generator[str, None, None]:
            yield self._sse_event({"type": "start", "conversation_id": conversation_id})
            for event in search_events:
                yield self._sse_event(event)
            reply_parts: list[str] = []
            stream = ai_client.chat_stream(
                system_prompt=system_prompt,
                messages=messages,
                max_output_tokens=settings.context_response_reserve,
            )
            while True:
                try:
                    delta = next(stream)
                except StopIteration as stop:
                    reply_usage = stop.value or TokenUsage()
                    usage.input_tokens += reply_usage.input_tokens
                    usage.output_tokens += reply_usage.output_tokens
                    usage.total_tokens += reply_usage.total_tokens
                    break
                reply_parts.append(delta)
                yield self._sse_event({"type": "delta", "content": delta})

            reply = "".join(reply_parts)
            self.memory_store.add_message(
                conversation_id,
                Message(role="assistant", content=reply, sources=sources, search_query=search_query),
            )
            self.memory_store.add_token_usage(conversation_id, usage)

            self.memory_store.session.commit()
            yield self._sse_event(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "usage": usage.model_dump(),
                }
            )

        return generate()

    def _extract_search_queries(
        self, ai_client: AIClient, message: str
    ) -> tuple[list[str], TokenUsage]:
        usage = TokenUsage()
        try:
            self.context_manager.validate_message(_QUERY_EXTRACT_PROMPT, message)
            reply, usage = ai_client.chat(
                system_prompt=_QUERY_EXTRACT_PROMPT,
                messages=[Message(role="user", content=message)],
                max_output_tokens=256,
            )
            queries = _parse_extracted_queries(reply)
            if queries:
                return queries, usage
        except Exception as exc:
            logger.warning("搜索词改写失败，回退启发式: %s", exc)
        return split_search_entities(build_search_query(message)), usage

    def _refine_search_queries(
        self, ai_client: AIClient, message: str, sources: list[SearchResult]
    ) -> tuple[list[str], TokenUsage]:
        """看完第一轮结果后决定要不要补搜：足够返回空列表，不足返回补充查询词。"""
        usage = TokenUsage()
        if not sources:
            return [], usage
        lines = [f"用户问题：{message}", "", "第一轮搜索结果："]
        for index, source in enumerate(sources, start=1):
            lines.append(f"[{index}] {source.title} 摘要: {source.snippet[:200]}")
        content = "\n".join(lines)
        try:
            self.context_manager.validate_message(_REVIEW_SEARCH_PROMPT, content)
            reply, usage = ai_client.chat(
                system_prompt=_REVIEW_SEARCH_PROMPT,
                messages=[Message(role="user", content=content)],
                max_output_tokens=256,
            )
            verdict = next((ln.strip() for ln in reply.splitlines() if ln.strip()), "")
            if not verdict or verdict.upper() in {"ENOUGH", "SUFFICIENT"} or verdict == "足够":
                return [], usage
            return _parse_extracted_queries(reply)[:_MAX_REFINE_QUERIES], usage
        except Exception as exc:
            logger.warning("补搜审查失败，跳过补搜: %s", exc)
            return [], usage

    def _search_with_refine(
        self,
        request: ChatRequest,
        ai_client: AIClient,
        queries: list[str] | None,
        events: list[dict] | None = None,
    ) -> tuple[str, list[SearchResult], str | None, str | None, TokenUsage]:
        """返回 (增强提示词, 合并来源, 搜索词, 错误, 补搜用量)。

        第一轮搜索成功后让模型审查结果充分性，不足则补搜一轮（≤_MAX_REFINE_QUERIES
        条、去掉与第一轮重复的查询）并按 URL 去重合并；补搜无收获时保留第一轮。
        events 非 None 时把各轮 search_start/search_result/search_error 追加进去供 SSE。
        """
        system_prompt, sources, error = self._search_context(request, queries)
        label = " / ".join(queries) if queries else None
        usage = TokenUsage()
        if events is not None:
            if error:
                events.append({"type": "search_error", "message": error})
            else:
                events.append(
                    {
                        "type": "search_result",
                        "query": label,
                        "results": [source.model_dump() for source in sources],
                    }
                )
        if not request.web_search or error or not sources:
            return system_prompt, sources, label, error, usage

        refine_queries, usage = self._refine_search_queries(
            ai_client, request.message, sources
        )
        new_queries = [q for q in refine_queries if q not in (queries or [])]
        if not new_queries:
            return system_prompt, sources, label, error, usage

        if events is not None:
            events.append({"type": "search_start", "query": " / ".join(new_queries)})
        extra, _ = self._search_round(new_queries)
        combined = self._merge_sources(sources, extra)
        new_label = " / ".join((queries or []) + new_queries)
        if events is not None:
            events.append(
                {
                    "type": "search_result",
                    "query": new_label,
                    "results": [source.model_dump() for source in combined],
                }
            )
        return self._build_search_prompt(new_label, combined), combined, new_label, None, usage

    def _merge_sources(self, *rounds: list[SearchResult]) -> list[SearchResult]:
        # 跨轮按 URL 去重、先第一轮后补搜轮，总量封顶避免提示词膨胀
        cap = self.web_search_service.max_results * 3
        merged: list[SearchResult] = []
        seen: set[str] = set()
        for results in rounds:
            for source in results:
                if source.url not in seen:
                    seen.add(source.url)
                    merged.append(source)
                    if len(merged) >= cap:
                        return merged
        return merged

    def _search_context(
        self, request: ChatRequest, queries: list[str] | None = None
    ) -> tuple[str, list[SearchResult], str | None]:
        """返回 (增强系统提示词, 合并来源, 错误信息)。

        未开启搜索原样返回；全部失败/无结果退回普通提示词并带错误信息，不阻塞回复。
        搜索结果只注入本轮，不写入会话记忆。
        """
        if not request.web_search:
            return self.system_prompt, [], None

        queries = queries or split_search_entities(build_search_query(request.message))
        merged, error = self._search_round(queries)
        if error or not merged:
            return self.system_prompt, [], error or "未找到相关搜索结果"

        label = " / ".join(queries)
        return self._build_search_prompt(label, merged), merged, None

    def _search_round(self, queries: list[str]) -> tuple[list[SearchResult], str | None]:
        """单轮搜索：逐实体搜索+噪声门控+去重轮转合并，返回 (本轮来源, 错误)。

        多实体时逐实体搜索再按 URL 去重、轮转合并，保证每个实体都有代表；
        部分实体失败不影响其余。某实体返回无关噪声页（结果无一提及查询词）时
        用精确匹配重试一次，仍无关则丢弃该实体，避免垃圾来源污染提示词。
        """
        per_entity: list[list[SearchResult]] = []
        first_error: str | None = None
        for query in queries:
            try:
                results = self.web_search_service.search(query)
            except WebSearchError as exc:
                first_error = first_error or str(exc)
                continue
            if not results_mention_query(query, results):
                # Bing 偶发返回与实体无关的门户/缓存垃圾页；精确匹配重试一次，仍无关则丢弃该实体
                try:
                    results = self.web_search_service.search(f'"{query}"')
                except WebSearchError:
                    results = []
                if not results_mention_query(query, results):
                    continue
            per_entity.append(results)

        if not per_entity:
            return [], first_error or "未找到相关搜索结果"

        cap = self.web_search_service.max_results * 2
        merged: list[SearchResult] = []
        seen: set[str] = set()
        index = 0
        while len(merged) < cap:
            added = False
            for results in per_entity:
                if index < len(results):
                    source = results[index]
                    if source.url not in seen:
                        seen.add(source.url)
                        merged.append(source)
                        added = True
            index += 1
            if not added:
                break

        if not merged:
            return [], "未找到相关搜索结果"
        return merged, None

    def _build_search_prompt(self, query: str, sources: list[SearchResult]) -> str:
        lines = [
            self.system_prompt,
            "",
            "# 联网搜索参考",
            f"以下是针对用户当前问题实时搜索到的网页结果（搜索词：{query}）。"
            "请优先参考这些结果作答，并在引用信息处用 Markdown 链接标注来源，"
            "例如 [标题](url)。结果可能过时或与问题无关，请自行甄别；"
            "不足以回答时结合自身知识并说明。",
            "",
        ]
        for index, source in enumerate(sources, start=1):
            snippet = source.snippet[:_SNIPPET_LIMIT]
            lines.append(
                f"[{index}] {source.title}\nURL: {source.url}\n摘要: {snippet}"
            )
        return "\n".join(lines)

    @staticmethod
    def _sse_event(data: dict) -> str:
        # 把数据序列化为一条 SSE 消息
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
