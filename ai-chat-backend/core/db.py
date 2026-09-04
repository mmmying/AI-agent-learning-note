import sqlite3
from pathlib import Path

from core.paths import DB_PATH

# 默认数据库文件路径
DEFAULT_DB_PATH = DB_PATH


class Database:
    """封装 SQLite 数据库文件路径与连接的创建"""

    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path
        # 确保数据库文件所在目录存在，如果不存在就自动创建
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        # 每次操作建立新连接，避免跨线程共用连接的问题（FastAPI 请求可能在不同线程中处理）
        conn = sqlite3.connect(self.path)
        # SQLite 默认不启用外键约束，需要每个连接手动开启
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
