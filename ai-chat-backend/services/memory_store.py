from uuid import uuid4

from core.db import Database
from models.schemas import ConversationSummary, Message

# 会话标题的最大长度（超出部分截断）
TITLE_MAX_LENGTH = 50


class MemoryStore:
    # 表结构由 migrations/ 目录下的迁移文件维护，应用启动时统一执行（见 main.py）
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    def create_conversation_id(self) -> str:
        return str(uuid4())

    # 获取历史会话列表（按最近更新时间倒序）
    def list_conversations(self) -> list[ConversationSummary]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at FROM conversations
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [
            ConversationSummary(
                id=id_, title=title, created_at=created_at, updated_at=updated_at
            )
            for id_, title, created_at, updated_at in rows
        ]

    # 删除指定会话（其消息通过外键级联一并删除），返回是否实际删除
    def delete_conversation(self, conversation_id: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    # 判断指定会话是否存在
    def conversation_exists(self, conversation_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return row is not None

    # 获取指定对话的对话消息（按插入顺序）
    def get_messages(self, conversation_id: str) -> list[Message]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM messages
                WHERE conversation_id = ?
                ORDER BY id
                """,
                (conversation_id,),
            ).fetchall()
        return [Message(role=role, content=content) for role, content in rows]

    # 向指定对话中添加对话消息
    def add_message(self, conversation_id: str, message: Message) -> None:
        with self.db.connect() as conn:
            # 写入时显式使用本地时区时间，不依赖建表时的列默认值
            # 会话不存在时先创建，标题取该会话第一条消息的内容（截断）
            conn.execute(
                """
                INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at)
                VALUES (?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                """,
                (conversation_id, message.content[:TITLE_MAX_LENGTH]),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
                """,
                (conversation_id, message.role, message.content),
            )
            # 有新消息时刷新会话的更新时间
            conn.execute(
                """
                UPDATE conversations SET updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (conversation_id,),
            )
