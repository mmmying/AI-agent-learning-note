from collections.abc import Generator

from core.config import settings
from models.schemas import Message
from services.factory import AIProviderFactory, Provider


class AIClient:
    def __init__(
        self,
        provider: Provider | None = None,
        api_key: str | None = None,
    ):
        provider = provider or settings.ai_provider

        self.strategy = AIProviderFactory.create(
            provider=provider,
            api_key=api_key,
        )

    def chat(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> str:

        return self.strategy.chat(
            system_prompt,
            messages,
        )

    def chat_stream(
        self,
        system_prompt: str,
        messages: list[Message],
    ) -> Generator[str, None, None]:

        yield from self.strategy.chat_stream(
            system_prompt,
            messages,
        )
