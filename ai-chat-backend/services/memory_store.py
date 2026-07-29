import sqlite3
from pathlib import Path
from uuid import uuid4

from models.schemas import ConversationSummary, Message

# 会话标题的最大长度（超出部分截断）
TITLE_MAX_LENGTH = 50


class MemoryStore:
    def __init__(self, path: Path = Path("data/chat.db")) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # 每次操作建立新连接，避免跨线程共用连接的问题（FastAPI 请求可能在不同线程中处理）
        conn = sqlite3.connect(self.path)
        # SQLite 默认不启用外键约束，需要每个连接手动开启
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        # 初始化数据库表结构：一个 conversation 包含多条 message
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations (id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
                ON messages (conversation_id)
                """
            )

    def create_conversation_id(self) -> str:
        return str(uuid4())

    # 获取历史会话列表（按最近更新时间倒序）
    def list_conversations(self) -> list[ConversationSummary]:
        with self._connect() as conn:
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
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    # 判断指定会话是否存在
    def conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return row is not None

    # 获取指定对话的对话消息（按插入顺序）
    def get_messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as conn:
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
        with self._connect() as conn:
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
