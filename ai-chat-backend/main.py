from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import APIError, AuthenticationError, RateLimitError

from api.chat import router as chat_router
from api.index import router as index_router
from core.logger import setup_logging

setup_logging()

# 创建整个应用
app = FastAPI()


@app.exception_handler(RateLimitError)
async def openai_rate_limit_handler(_: Request, exc: RateLimitError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": "AI API 请求受限：账户配额不足或触发限流，请检查计费与 API 额度",
            "error": str(exc),
        },
    )


@app.exception_handler(AuthenticationError)
async def openai_auth_handler(_: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "detail": "AI API 密钥无效或未授权，请检查 .env 中的 OPENAI_API_KEY 或 DEEPSEEK_API_KEY",
            "error": str(exc),
        },
    )


@app.exception_handler(APIError)
async def openai_api_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "detail": "调用 AI API 失败",
            "error": str(exc),
        },
    )


# 把 Router 注册到 FastAPI 应用中
app.include_router(index_router)
app.include_router(chat_router)
