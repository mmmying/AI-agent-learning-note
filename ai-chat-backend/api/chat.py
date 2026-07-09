from fastapi import APIRouter
from models.schemas import ChatRequest, ChatResponse
from services.chat_service import ChatService

# 创建一个路由模块
router = APIRouter(
    prefix="/chat",  # 路由前缀
    tags=["chat"],  # 标签，用于分组路由
)


chat_service = ChatService()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return chat_service.chat(request)
