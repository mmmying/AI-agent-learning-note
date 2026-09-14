from openai import OpenAI
from core.config import settings
from services.strategy import OpenAICompatibleStrategy


class OpenAIStrategy(OpenAICompatibleStrategy):

    def __init__(self, api_key: str | None = None):

        key = api_key or settings.openai_api_key

        if not key:
            raise ValueError("OPENAI_API_KEY 未设置，请在 .env 中配置")

        client = OpenAI(
            api_key=key,
            timeout=settings.timeout,
        )

        super().__init__(
            client=client,
            model=settings.openai_model_name,
        )
