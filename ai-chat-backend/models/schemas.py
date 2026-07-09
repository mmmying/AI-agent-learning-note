# 数据模型
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["openai", "deepseek"]


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1)
    provider: Provider | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str


class Message(BaseModel):
    role: str
    content: str
