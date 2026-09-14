from abc import ABC, abstractmethod
from typing import Generator
from models.schemas import Message
from openai import OpenAI

from typing import Literal

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
    ) -> str:
        pass

    @abstractmethod
    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> Generator[str, None, None]:
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
    ) -> str:
        input_messages = self._build_input_messages(
            system_prompt,
            messages,
        )

        response = self.client.responses.create(
            **self._build_request_kwargs(input_messages),
        )

        return response.output_text

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> Generator[str, None, None]:

        input_messages = self._build_input_messages(
            system_prompt,
            messages,
        )

        stream = self.client.responses.create(
            **self._build_request_kwargs(input_messages),
            stream=True,
        )

        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta
