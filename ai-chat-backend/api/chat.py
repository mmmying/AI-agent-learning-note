from db.dependencies import SessionDep
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from models.schemas import ChatRequest, ChatResponse
from services.chat_service import ChatService
from services.memory_store import MemoryStore

# 创建一个路由模块
router = APIRouter(
    prefix="/chat",  # 路由前缀
    tags=["chat"],  # 标签，用于分组路由
)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, session: SessionDep) -> ChatResponse:
    chat_service = ChatService(memory_store=MemoryStore(session=session))
    return chat_service.chat(request)


@router.post("/stream")
def chat_stream(request: ChatRequest, session: SessionDep) -> StreamingResponse:
    chat_service = ChatService(memory_store=MemoryStore(session=session))
    # 流式对话接口，以 SSE（text/event-stream）逐段返回回复
    return StreamingResponse(
        chat_service.chat_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # 禁止代理缓存流式响应
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证逐段推送
        },
    )
