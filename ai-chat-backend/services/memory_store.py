from uuid import uuid4

from db.database import Database
from db.orm_models import Conversation, MessageRecord
from models.schemas import ConversationSummary, Message
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert

# 会话标题的最大长度（超出部分截断）
TITLE_MAX_LENGTH = 50


class MemoryStore:
    # 表结构由 db.database 的启动初始化入口统一管理。
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    def create_conversation_id(self) -> str:
        return str(uuid4())

    # 获取历史会话列表（按最近更新时间倒序）
    def list_conversations(self) -> list[ConversationSummary]:
        with self.db.session() as session:
            conversations = session.scalars(
                select(Conversation).order_by(Conversation.updated_at.desc())
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
        with self.db.session() as session:
            result = session.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            return result.rowcount > 0

    # 判断指定会话是否存在
    def conversation_exists(self, conversation_id: str) -> bool:
        with self.db.session() as session:
            return session.get(Conversation, conversation_id) is not None

    # 获取指定对话的对话消息（按插入顺序）
    def get_messages(self, conversation_id: str) -> list[Message]:
        with self.db.session() as session:
            records = session.scalars(
                select(MessageRecord)
                .where(MessageRecord.conversation_id == conversation_id)
                .order_by(MessageRecord.id)
            )
            return [Message(role=row.role, content=row.content) for row in records]

    # 向指定对话中添加对话消息
    def add_message(self, conversation_id: str, message: Message) -> None:
        with self.db.session() as session:
            now = func.datetime("now", "localtime")
            # 原子 upsert 兼容并发创建，并保留首条消息生成的标题。
            session.execute(
                insert(Conversation)
                .values(
                    id=conversation_id,
                    title=message.content[:TITLE_MAX_LENGTH],
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[Conversation.id])
            )
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
                .where(Conversation.id == conversation_id)
                .values(updated_at=now)
            )
