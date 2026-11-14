from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Literal

from models.schemas import Message, TokenUsage
from openai import OpenAI
from openai.types.responses import ResponseUsage

Provider = Literal[
    "openai",
    "deepseek",
]


# 定义策略接口，不同的AI提供商都必须实现chat和chat_stream方法
class AIProviderStrategy(ABC):
    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        messages: list[Message],
        *,
        max_output_tokens: int | None = None,
    ) -> tuple[str, TokenUsage]:
        pass

    @abstractmethod
    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
        *,
        max_output_tokens: int | None = None,
    ) -> Generator[str, None, TokenUsage]:
        pass


class OpenAICompatibleStrategy(AIProviderStrategy):
    def __init__(
        self,
        client: OpenAI,
        model: str,
    ):
        self.client = client
        self.model = model

    def _build_input_messages(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> list[dict[str, str]]:
        return [
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

    def _build_request_kwargs(
        self,
        input_messages: list[dict[str, str]],
    ) -> dict:
        return {
            "model": self.model,
            "input": input_messages,
        }

    def chat(
        self,
        system_prompt: str,
        messages: list[Message],
        *,
        max_output_tokens: int | None = None,
    ) -> tuple[str, TokenUsage]:
        input_messages = self._build_input_messages(
            system_prompt,
            messages,
        )

        kwargs = self._build_request_kwargs(input_messages)
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        response = self.client.responses.create(**kwargs)

        return response.output_text, self._extract_usage(response.usage)

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
        *,
        max_output_tokens: int | None = None,
    ) -> Generator[str, None, TokenUsage]:

        input_messages = self._build_input_messages(
            system_prompt,
            messages,
        )

        kwargs = self._build_request_kwargs(input_messages)
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        stream = self.client.responses.create(**kwargs, stream=True)

        usage = TokenUsage()
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta
            elif event.type in ("response.completed", "response.incomplete"):
                usage = self._extract_usage(event.response.usage)
        return usage

    @staticmethod
    def _extract_usage(usage: ResponseUsage | None) -> TokenUsage:
        # 上游未返回 usage（如响应中断）时按 0 计，避免统计阻塞回复。
        if usage is None:
            return TokenUsage()
        return TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )
