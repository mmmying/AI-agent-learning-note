from core.config import settings
from fastapi import APIRouter, Header

# 创建一个路由模块
router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {
        "message": f"{settings.app_name} is running",
        "env": settings.app_env,
    }


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


@router.get("/test")
def test(user_agent: str = Header()):
    return {"user_agent": user_agent}
