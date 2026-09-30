# 负责数据库连接、session工厂和初始化表
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.config import settings
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import Base


class Database:
    """管理引擎，每次操作使用独立 Session 和事务。"""

    def __init__(self, path: Path | None = None) -> None:
        # 默认读取统一配置，显式 path 用于测试等场景。
        self.path = Path(settings.db_path if path is None else path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={
                "check_same_thread": False
            },  # 这是 SQLite + Python 中比较常见的配置
            echo=settings.debug,
        )
        event.listen(
            self.engine, "connect", self._enable_foreign_keys
        )  # 每当 Engine 建立一个新的数据库连接时，执行 _enable_foreign_keys
        self._sessions = sessionmaker(bind=self.engine)

    def init_tables(self) -> None:
        """根据 ORM 元数据创建缺失的表，不覆盖已有数据。"""
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "PRAGMA foreign_keys = ON"
            )  # 这是 SQLite 特有的配置，需要确保 SQLite 真正启用了外键约束
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        # 成功提交、异常回滚，退出时关闭 Session。
        with self._sessions.begin() as session:
            yield session


def init_db(db: Database | None = None) -> None:
    """应用启动入口：根据 ORM 模型创建缺失的表。"""

    owns_db = db is None
    db = db or Database()
    try:
        db.init_tables()
    finally:
        if owns_db:
            db.engine.dispose()
