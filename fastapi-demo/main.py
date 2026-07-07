from fastapi import FastAPI
from api.index import router as index_router
from api.chat import router as chat_router
from core.logger import setup_logging

setup_logging()

# 创建整个应用
app = FastAPI()

# 把 Router 注册到 FastAPI 应用中
app.include_router(index_router)
app.include_router(chat_router)
