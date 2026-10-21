from db.dependencies import SessionDep
from fastapi import APIRouter, HTTPException
from models.schemas import ConversationSummary, Message
from services.memory_store import MemoryStore

# 创建一个路由模块
router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(session: SessionDep) -> list[ConversationSummary]:
    memory_store = MemoryStore(session=session)
    # 获取历史会话列表（按最近更新时间倒序）
    return memory_store.list_conversations()


@router.get("/{conversation_id}/messages", response_model=list[Message])
def get_conversation_messages(
    conversation_id: str, session: SessionDep
) -> list[Message]:
    memory_store = MemoryStore(session=session)
    # 获取指定会话的消息列表（按时间顺序）
    if not memory_store.conversation_exists(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return memory_store.get_messages(conversation_id)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, session: SessionDep) -> None:
    memory_store = MemoryStore(session=session)
    # 删除指定会话及其全部消息
    if not memory_store.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    session.commit()
