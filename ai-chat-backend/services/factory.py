from typing import Literal
from services.strategy import AIProviderStrategy
from services.providers.openai_strategy import OpenAIStrategy
from services.providers.deepseek_strategy import DeepSeekStrategy
from services.strategy import Provider


class AIProviderFactory:

    @staticmethod
    def create(
        provider: Provider,
        api_key: str | None = None,
    ) -> AIProviderStrategy:

        ## 根据provider创建具体策略
        if provider == "openai":
            return OpenAIStrategy(api_key)

        if provider == "deepseek":
            return DeepSeekStrategy(api_key)

        raise ValueError(f"不支持的 API: {provider}")
