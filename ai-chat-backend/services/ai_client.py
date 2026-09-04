from typing import Generator, Literal

from openai import OpenAI

from core.config import settings
from models.schemas import Message

Provider = Literal["openai", "deepseek"]


class AIClient:

    def __init__(
        self,
        provider: Provider | None = None,
        api_key: str | None = None,
    ):
        self.provider: Provider = provider or settings.ai_provider

        if self.provider == "deepseek":
            key = api_key or settings.deepseek_api_key
            if not key:
                raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 中配置")
            self.client = OpenAI(
                api_key=key,
                base_url=settings.deepseek_base_url,
                timeout=settings.timeout,
            )
            self.model = settings.deepseek_model_name
            return

        elif self.provider == "openai":
            key = api_key or settings.openai_api_key
            if not key:
                raise ValueError("OPENAI_API_KEY 未设置，请在 .env 中配置")
            self.client = OpenAI(api_key=key, timeout=settings.timeout)
            self.model = settings.openai_model_name

        else:
            raise ValueError(f"不支持的 API: {self.provider}")

    # 内部方法，构建 Responses API 请求参数
    def _build_request_kwargs(
        self,
        input_messages: list[dict[str, str]],
    ) -> dict:
        kwargs: dict = {
            "model": self.model,
            "input": input_messages,
        }

        if self.provider == "deepseek":
            # Responses API 用 reasoning.effort 同时控制思考开关与强度，none 表示关闭思考
            kwargs["reasoning"] = {
                "effort": (
                    settings.deepseek_reasoning_effort
                    if settings.deepseek_thinking == "enabled"
                    else "none"
                ),
            }

        return kwargs

    def _build_input_messages(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> list[dict[str, str]]:
        # 构建带系统提示词的完整消息列表
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

    def chat(self, system_prompt: str, messages: list[Message]) -> str:
        input_messages = self._build_input_messages(system_prompt, messages)
        response = self.client.responses.create(
            **self._build_request_kwargs(input_messages),
        )
        return response.output_text

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> Generator[str, None, None]:
        # 流式对话，逐段产出模型回复的文本增量
        input_messages = self._build_input_messages(system_prompt, messages)
        stream = self.client.responses.create(
            **self._build_request_kwargs(input_messages),
            stream=True,
        )
        for event in stream:
            # 思考模式下思维链走 response.reasoning_text.delta，这里只输出正文增量
            if event.type == "response.output_text.delta":
                yield event.delta
