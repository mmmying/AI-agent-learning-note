# 入口层：协调程序执行过程，只负责告诉系统下一步做什么
import asyncio
from client import AIClient
from fastapi import FastAPI
from logger import setup_logging
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/")
def root():
    return {"message": "server running"}


@app.get("/hello")
def hello():
    return {"reply": "hello"}


@app.get("/stream")
async def stream():
    return StreamingResponse(fake_stream(), media_type="text/event-stream")


# 模拟流式输出
async def fake_stream():
    words = ["你好，", "我是", "一个", "AI", "助手。"]
    for word in words:
        await asyncio.sleep(1)
        yield word


def main() -> None:
    setup_logging()

    client = AIClient()

    result = client.send("hello")

    print(result)


if __name__ == "__main__":
    main()
