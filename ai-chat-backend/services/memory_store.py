from uuid import uuid4
from fastapi import HTTPException

from db.orm_models import Conversation, MessageRecord
from models.schemas import ConversationSummary, Message
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

# 会话标题的最大长度（超出部分截断）
TITLE_MAX_LENGTH = 50


class MemoryStore:
    # 表结构由 db.database 的启动初始化入口统一管理。
    # 事务和 Session 生命周期由调用方管理，存储层不提交或关闭 Session。
    # 接收 Session，只负责数据库读写
    def __init__(self, session: Session, user_id: str | None = None) -> None:
        self.session = session
        self.user_id = user_id

    def create_conversation_id(self) -> str:
        return str(uuid4())

    # 获取历史会话列表（按最近更新时间倒序）
    def list_conversations(self) -> list[ConversationSummary]:
        session = self.session
        conversations = session.scalars(
            select(Conversation).where(Conversation.user_id == self.user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return [
            ConversationSummary(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in conversations
        ]

    # 删除指定会话（其消息通过外键级联一并删除），返回是否实际删除
    def delete_conversation(self, conversation_id: str) -> bool:
        session = self.session
        result = session.execute(
            delete(Conversation).where(Conversation.id == conversation_id,
                                       Conversation.user_id == self.user_id)
        )
        return result.rowcount > 0

    # 判断指定会话是否存在
    def conversation_exists(self, conversation_id: str) -> bool:
        session = self.session
        return session.scalar(select(Conversation.id).where(
            Conversation.id == conversation_id, Conversation.user_id == self.user_id
        )) is not None

    def require_conversation(self, conversation_id: str) -> None:
        if not self.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")

    # 获取指定对话的对话消息（按插入顺序）
    def get_messages(self, conversation_id: str) -> list[Message]:
        session = self.session
        records = session.scalars(
            select(MessageRecord)
            .join(Conversation)
            .where(MessageRecord.conversation_id == conversation_id,
                   Conversation.user_id == self.user_id)
            .order_by(MessageRecord.id)
        )
        return [Message(role=row.role, content=row.content) for row in records]

    # 向指定对话中添加对话消息
    # 完成了三个关联操作：确保会话存在 → 写入消息 → 更新时间
    def add_message(self, conversation_id: str, message: Message) -> None:
        session = self.session
        now = func.datetime("now", "localtime")
        # 原子 upsert 兼容并发创建，并保留首条消息生成的标题。
        session.execute(
            insert(Conversation)
            .values(
                id=conversation_id,
                user_id=self.user_id,
                title=message.content[:TITLE_MAX_LENGTH],
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[Conversation.id])
        )
        # 即使 ID 冲突，也绝不向其他用户或未分配的会话写入消息。
        self.require_conversation(conversation_id)
        session.add(
            MessageRecord(
                conversation_id=conversation_id,
                role=message.role,
                content=message.content,
                created_at=now,
            )
        )
        session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == self.user_id)
            .values(updated_at=now)
        )
        session.flush()
