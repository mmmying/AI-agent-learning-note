from typing import Literal

from openai import OpenAI

from core.config import (
    AI_PROVIDER,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_NAME,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_THINKING,
    MODEL_NAME,
    OPENAI_API_KEY,
    TIMEOUT,
)
from models.schemas import Message

Provider = Literal["openai", "deepseek"]


class AIClient:

    def __init__(
        self,
        provider: Provider | None = None,
        api_key: str | None = None,
    ):
        self.provider: Provider = provider or AI_PROVIDER

        if self.provider == "deepseek":
            key = api_key or DEEPSEEK_API_KEY
            if not key:
                raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 中配置")
            self.client = OpenAI(
                api_key=key,
                base_url=DEEPSEEK_BASE_URL,
                timeout=TIMEOUT,
            )
            self.model = DEEPSEEK_MODEL_NAME
            return

        elif self.provider == "openai":
            key = api_key or OPENAI_API_KEY
            if not key:
                raise ValueError("OPENAI_API_KEY 未设置，请在 .env 中配置")
            self.client = OpenAI(api_key=key, timeout=TIMEOUT)
            self.model = MODEL_NAME

    def _build_deepseek_request_kwargs(
        self,
        input_messages: list[dict[str, str]],
    ) -> dict:
        thinking_enabled = DEEPSEEK_THINKING == "enabled"
        kwargs: dict = {
            "model": self.model,
            "messages": input_messages,
            "extra_body": {
                "thinking": {
                    "type": "enabled" if thinking_enabled else "disabled",
                },
            },
        }
        if thinking_enabled:
            kwargs["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
        return kwargs

    def chat(self, system_prompt: str, messages: list[Message]) -> str:
        input_messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            *[
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
        ]

        if self.provider == "deepseek":
            response = self.client.chat.completions.create(
                **self._build_deepseek_request_kwargs(input_messages),
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("DeepSeek API 返回空内容")
            return content

        elif self.provider == "openai":
            response = self.client.responses.create(
                model=self.model,
                input=input_messages,
            )
            return response.output_text

        else:
            raise ValueError(f"不支持的 API: {self.provider}")
