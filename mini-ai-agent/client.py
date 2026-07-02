# 核心业务层：负责和 AI 服务通信
import logging
from typing import Any

import requests

from config import API_URL, MODEL_NAME, TIMEOUT

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(
        self,
        api_url: str = API_URL,
        model: str = MODEL_NAME,
        timeout: int = TIMEOUT,
    ) -> None:
        self.api_url = api_url
        self.model = model
        self.timeout = timeout

    # 单一职责原则，将请求构造逻辑单独拆出来
    def create_payload(self, message: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": message,
        }

    def send(self, message: str) -> dict[str, Any]:
        # 构造请求
        payload = self.create_payload(message)

        try:
            logger.info("Sending request")
            # 发送请求
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=self.timeout,
            )
            # raise_for_status 是 Python requests 库里用来检查 HTTP 响应状态码的方法
            # 请求失败会自动抛出异常
            response.raise_for_status()

            logger.info("Request success")
            # 处理响应，请求成功才会执行到这里
            return response.json()

        except requests.RequestException:
            logger.exception("Request failed")
            raise
