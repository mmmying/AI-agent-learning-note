import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_URL = "https://jsonplaceholder.typicode.com/posts"  # 模拟假数据的网站
MODEL_NAME = "gpt-5"
DEEPSEEK_MODEL_NAME = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
TIMEOUT = 30
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").lower()
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high").lower()
