from typing import Generator, Literal

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
            # 思考强度通过 extra_body 传给 DeepSeek，避免污染标准参数
            kwargs["extra_body"]["reasoning"] = {"effort": DEEPSEEK_REASONING_EFFORT}
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

        if self.provider == "deepseek":
            # DeepSeek 只兼容 OpenAI 的 Chat Completions API（POST /chat/completions）
            response = self.client.chat.completions.create(
                **self._build_deepseek_request_kwargs(input_messages),
            )
            return response.choices[0].message.content or ""

        elif self.provider == "openai":
            response = self.client.responses.create(
                model=self.model,
                input=input_messages,
            )
            return response.output_text

        else:
            raise ValueError(f"不支持的 API: {self.provider}")

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> Generator[str, None, None]:
        # 流式对话，逐段产出模型回复的文本增量
        input_messages = self._build_input_messages(system_prompt, messages)

        if self.provider == "deepseek":
            stream = self.client.chat.completions.create(
                **self._build_deepseek_request_kwargs(input_messages),
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                # 思考模式下会先产出 reasoning_content 字段，这里只输出正文增量
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

        elif self.provider == "openai":
            stream = self.client.responses.create(
                model=self.model,
                input=input_messages,
                stream=True,
            )
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta

        else:
            raise ValueError(f"不支持的 API: {self.provider}")
