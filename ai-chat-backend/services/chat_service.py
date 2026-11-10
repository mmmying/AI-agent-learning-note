import json
from collections.abc import Generator

from core.paths import SYSTEM_PROMPT_FILE
from models.schemas import ChatRequest, ChatResponse, Message, TokenUsage
from services.ai_client import AIClient
from services.memory_store import MemoryStore


class ChatService:
    # 初始化建立存储依赖和系统提示词
    def __init__(self, memory_store: MemoryStore) -> None:
        self.memory_store = memory_store
        self.system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")

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

        reply, usage = ai_client.chat(
            system_prompt=self.system_prompt,
            messages=messages,
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="assistant", content=reply),
        )
        self.memory_store.add_token_usage(conversation_id, usage)

        # 一轮对话作为一个事务，持久化成功后再返回回复。
        self.memory_store.session.commit()
        return ChatResponse(
            conversation_id=conversation_id,
            reply=reply,
            usage=usage,
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

        reply_parts: list[str] = []
        usage = TokenUsage()
        stream = ai_client.chat_stream(
            system_prompt=self.system_prompt,
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
            Message(role="assistant", content=reply),
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

    @staticmethod
    def _sse_event(data: dict) -> str:
        # 把数据序列化为一条 SSE 消息
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
