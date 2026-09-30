from core.config import settings
from openai import OpenAI
from services.strategy import OpenAICompatibleStrategy


class DeepSeekStrategy(OpenAICompatibleStrategy):
    def __init__(self, api_key: str | None = None):

        key = api_key or settings.deepseek_api_key

        if not key:
            raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 中配置")

        client = OpenAI(
            api_key=key,
            base_url=settings.deepseek_base_url,
            timeout=settings.timeout,
        )

        super().__init__(
            client=client,
            model=settings.deepseek_model_name,
        )

    def _build_request_kwargs(
        self,
        input_messages: list[dict[str, str]],
    ) -> dict:

        kwargs = super()._build_request_kwargs(input_messages)

        kwargs["reasoning"] = {
            "effort": (
                settings.deepseek_reasoning_effort
                if settings.deepseek_thinking == "enabled"
                else "none"
            ),
        }

        return kwargs
