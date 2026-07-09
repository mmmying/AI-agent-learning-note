from fastapi import APIRouter
from models.schemas import ChatRequest, ChatResponse
from services.chat_service import ChatService

# 创建一个路由模块
router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {"message": "server running"}


@router.get("/hello")
def hello() -> dict[str, str]:
    return {"reply": "hello"}


# Query Parameter
@router.get("/search")
def search(query: str):
    return {"reply": f"Searching for {query}"}


# Path Parameter
@router.get("/user/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}
