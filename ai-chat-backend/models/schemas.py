# 数据模型
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Provider = Literal["openai", "deepseek"]
Role = Literal["system", "user", "assistant"]


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


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
