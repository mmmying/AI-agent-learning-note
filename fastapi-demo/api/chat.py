from fastapi import APIRouter
from models.schemas import ChatRequest, ChatResponse
from services.chat_service import ChatService

# 创建一个路由模块
router = APIRouter(
    prefix="/chat",  # 路由前缀
    tags=["chat"],  # 标签，用于分组路由
)


@router.get("/")
def root() -> dict[str, str]:
    return {"message": "this is a chat route"}


@router.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    # 路由层只负责收请求、调服务、返回响应
    service = ChatService()
    reply = service.chat(request.message)

    return ChatResponse(reply=reply)
