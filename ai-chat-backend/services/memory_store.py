from uuid import uuid4

from db.dialect import insert_statement, now_expression
from db.orm_models import Conversation, MessageRecord
from fastapi import HTTPException
from models.schemas import ConversationSummary, Message, TokenUsage
from sqlalchemy import delete, select, update
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
            select(Conversation)
            .where(Conversation.user_id == self.user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return [
            ConversationSummary(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                usage=TokenUsage(
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    total_tokens=row.total_tokens,
                ),
            )
            for row in conversations
        ]

    # 删除指定会话（其消息通过外键级联一并删除），返回是否实际删除
    def delete_conversation(self, conversation_id: str) -> bool:
        session = self.session
        result = session.execute(
            delete(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
        )
        return result.rowcount > 0

    # 判断指定会话是否存在
    def conversation_exists(self, conversation_id: str) -> bool:
        session = self.session
        return (
            session.scalar(
                select(Conversation.id).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == self.user_id,
                )
            )
            is not None
        )

    def require_conversation(self, conversation_id: str) -> None:
        if not self.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")

    # 获取指定对话的对话消息（按插入顺序）
    def get_messages(self, conversation_id: str) -> list[Message]:
        session = self.session
        records = session.scalars(
            select(MessageRecord)
            .join(Conversation)
            .where(
                MessageRecord.conversation_id == conversation_id,
                Conversation.user_id == self.user_id,
            )
            .order_by(MessageRecord.id)
        )
        return [
            Message(
                role=row.role,
                content=row.content,
                sources=row.sources or [],
                search_query=row.search_query,
            )
            for row in records
        ]

    def get_context(self, conversation_id: str) -> tuple[str, list[MessageRecord]]:
        context = self.session.execute(
            select(Conversation.context_summary, Conversation.summary_through_id).where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
        ).one_or_none()
        if context is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        summary, through_id = context
        records = self.session.scalars(
            select(MessageRecord)
            .where(
                MessageRecord.conversation_id == conversation_id,
                MessageRecord.id > through_id,
            )
            .order_by(MessageRecord.id)
        ).all()
        return summary, list(records)

    # 摘要与覆盖游标原子更新，和消息共用调用方的事务。
    def save_context_summary(
        self, conversation_id: str, summary: str, through_id: int
    ) -> None:
        self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
            .values(context_summary=summary, summary_through_id=through_id)
        )

    # 向指定对话中添加对话消息
    # 完成了三个关联操作：确保会话存在 → 写入消息 → 更新时间
    def add_message(self, conversation_id: str, message: Message) -> None:
        session = self.session
        dialect = session.get_bind().dialect.name
        now = now_expression(dialect)
        # 原子 upsert 兼容并发创建，并保留首条消息生成的标题。
        session.execute(
            insert_statement(dialect)(Conversation)
            .values(
                id=conversation_id,
                user_id=self.user_id,
                title=message.content[:TITLE_MAX_LENGTH],
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[Conversation.id])
        )
        # 插入前锁定会话以保持消息与摘要游标顺序；SQLite 依靠 upsert 的写锁。
        locked_id = session.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
            .with_for_update()
        )
        if locked_id is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        session.add(
            MessageRecord(
                conversation_id=conversation_id,
                role=message.role,
                content=message.content,
                sources=[source.model_dump() for source in message.sources] or None,
                search_query=message.search_query,
                created_at=now,
            )
        )
        session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
            .values(updated_at=now)
        )
        session.flush()

    # 累加本轮 token 用量到会话累计值；与消息写入处于同一事务，由调用方提交。
    def add_token_usage(self, conversation_id: str, usage: TokenUsage) -> None:
        self.session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id, Conversation.user_id == self.user_id
            )
            .values(
                input_tokens=Conversation.input_tokens + usage.input_tokens,
                output_tokens=Conversation.output_tokens + usage.output_tokens,
                total_tokens=Conversation.total_tokens + usage.total_tokens,
            )
        )
