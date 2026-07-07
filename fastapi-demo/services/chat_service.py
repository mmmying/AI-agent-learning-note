import os

from services.ai_client import AIClient


class ChatService:
    """封装聊天业务逻辑，对外提供统一的 chat 接口。"""

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        self.client = AIClient(api_key=api_key)

    def chat(self, message: str) -> str:
        """调用 AI 客户端生成回复。"""
        return self.client.chat(message)
