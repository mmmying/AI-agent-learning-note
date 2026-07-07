from openai import OpenAI

from core.config import MODEL_NAME, TIMEOUT


class AIClient:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, timeout=TIMEOUT)  # 官方 api

    def chat(self, message: str) -> str:

        response = self.client.responses.create(
            model=MODEL_NAME,
            input=message,
        )

        return response.output_text

