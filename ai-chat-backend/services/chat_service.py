import json
import logging
from collections.abc import Generator

from core.paths import SYSTEM_PROMPT_FILE
from models.schemas import ChatRequest, ChatResponse, Message, SearchResult, TokenUsage
from services.ai_client import AIClient
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

    def chat(self, request: ChatRequest) -> ChatResponse:
        ai_client = AIClient(provider=request.provider)
        conversation_id = (
            request.conversation_id or self.memory_store.create_conversation_id()
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="user", content=request.message),
        )

        messages = self.memory_store.get_messages(conversation_id)
        queries = (
            self._extract_search_queries(ai_client, request.message)
            if request.web_search
            else None
        )
        search_query = " / ".join(queries) if queries else None
        system_prompt, sources, _ = self._search_context(request, queries)

        reply, usage = ai_client.chat(
            system_prompt=system_prompt,
            messages=messages,
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

        messages = self.memory_store.get_messages(conversation_id)

        # 先把 conversation_id 发给客户端，方便新对话时前端立刻拿到会话 ID
        yield self._sse_event({"type": "start", "conversation_id": conversation_id})

        # 搜索与搜索词改写都是阻塞调用，先发 search_start 让前端立刻显示“正在联网搜索”；
        # 这里的 query 是启发式 provisional，改写完成后由 search_result 带最终搜索词。
        if request.web_search:
            yield self._sse_event(
                {"type": "search_start", "query": build_search_query(request.message)}
            )
            queries = self._extract_search_queries(ai_client, request.message)
            search_query = " / ".join(queries)
        else:
            queries = None
            search_query = None
        system_prompt, sources, search_error = self._search_context(request, queries)
        if request.web_search:
            if search_error:
                yield self._sse_event({"type": "search_error", "message": search_error})
            else:
                yield self._sse_event(
                    {
                        "type": "search_result",
                        "query": search_query,
                        "results": [source.model_dump() for source in sources],
                    }
                )

        reply_parts: list[str] = []
        usage = TokenUsage()
        stream = ai_client.chat_stream(
            system_prompt=system_prompt,
            messages=messages,
        )
        # 逐段转发增量；流结束后生成器的返回值即本轮 token 用量。
        while True:
            try:
                delta = next(stream)
            except StopIteration as stop:
                usage = stop.value or TokenUsage()
                break
            reply_parts.append(delta)
            yield self._sse_event({"type": "delta", "content": delta})

        reply = "".join(reply_parts)
        self.memory_store.add_message(
            conversation_id,
            Message(role="assistant", content=reply, sources=sources, search_query=search_query),
        )
        self.memory_store.add_token_usage(conversation_id, usage)

        # 提交失败时不发送 done，由 Session 依赖兜底回滚。
        self.memory_store.session.commit()
        yield self._sse_event(
            {
                "type": "done",
                "conversation_id": conversation_id,
                "usage": usage.model_dump(),
            }
        )

    def _extract_search_queries(self, ai_client: AIClient, message: str) -> list[str]:
        """用模型把消息改写成搜索词列表；失败/为空时回退启发式拆分。"""
        try:
            reply, _ = ai_client.chat(
                system_prompt=_QUERY_EXTRACT_PROMPT,
                messages=[Message(role="user", content=message)],
            )
            queries = _parse_extracted_queries(reply)
            if queries:
                return queries
        except Exception as exc:
            logger.warning("搜索词改写失败，回退启发式: %s", exc)
        return split_search_entities(build_search_query(message))

    def _search_context(
        self, request: ChatRequest, queries: list[str] | None = None
    ) -> tuple[str, list[SearchResult], str | None]:
        """返回 (增强系统提示词, 合并来源, 错误信息)。

        多实体时逐实体搜索再按 URL 去重、轮转合并，保证每个实体都有代表；
        部分实体失败不影响其余。某实体返回无关噪声页（结果无一提及查询词）时
        用精确匹配重试一次，仍无关则丢弃该实体，避免垃圾来源污染提示词。
        未开启搜索原样返回；全部失败/无结果退回普通提示词并带错误信息，不阻塞回复。
        搜索结果只注入本轮，不写入会话记忆。
        """
        if not request.web_search:
            return self.system_prompt, [], None

        queries = queries or split_search_entities(build_search_query(request.message))
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
            return self.system_prompt, [], first_error or "未找到相关搜索结果"

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
            return self.system_prompt, [], "未找到相关搜索结果"

        label = " / ".join(queries)
        return self._build_search_prompt(label, merged), merged, None

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
