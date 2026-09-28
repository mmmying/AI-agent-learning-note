# 数据模型
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["openai", "deepseek"]
Role = Literal["system", "user", "assistant"]


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1)
    provider: Provider | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str


class Message(BaseModel):
    role: Role
    content: str


class ConversationSummary(BaseModel):
    # 会话摘要，用于历史会话列表展示
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
