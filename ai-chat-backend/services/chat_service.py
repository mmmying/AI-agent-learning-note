from pathlib import Path

from models.schemas import ChatRequest, ChatResponse, Message
from services.ai_client import AIClient
from services.memory_store import MemoryStore


class ChatService:
    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self.memory_store = memory_store or MemoryStore()
        self.system_prompt = Path("prompts/system.md").read_text(encoding="utf-8") # 读取系统提示词

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

        reply = ai_client.chat(
            system_prompt=self.system_prompt,
            messages=messages,
        )

        self.memory_store.add_message(
            conversation_id,
            Message(role="assistant", content=reply),
        )

        return ChatResponse(
            conversation_id=conversation_id,
            reply=reply,
        )
