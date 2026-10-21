"""供 FastAPI 路由使用的服务依赖。"""

from typing import Annotated

from db.dependencies import SessionDep
from fastapi import Depends
from services.chat_service import ChatService
from services.memory_store import MemoryStore


def get_memory_store(session: SessionDep) -> MemoryStore:
    """使用请求级 Session 创建会话存储。"""
    return MemoryStore(session=session)


MemoryStoreDep = Annotated[MemoryStore, Depends(get_memory_store)]


def get_chat_service(memory_store: MemoryStoreDep) -> ChatService:
    """使用注入的会话存储创建聊天服务。"""
    return ChatService(memory_store=memory_store)


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
