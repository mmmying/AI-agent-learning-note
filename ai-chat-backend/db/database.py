# 负责数据库连接、session工厂和初始化表
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.config import settings
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import Base


class Database:
    """管理引擎和 Session 生命周期，事务由业务层显式提交。"""

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

        # 每当 Engine 建立一个新的数据库连接时，执行 _enable_foreign_keys
        event.listen(self.engine, "connect", self._enable_foreign_keys)

        # sessionmaker 返回一个工厂，之后调用工厂就能创建新的 Session
        self._sessions = sessionmaker(bind=self.engine)

    def init_tables(self) -> None:
        """根据 ORM 元数据创建缺失的表，不覆盖已有数据。"""
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _enable_foreign_keys(connection, _record) -> None:
        """SQLite 默认不会自动开启外键，需要显式开启。"""
        cursor = connection.cursor()
        try:
            cursor.execute(
                "PRAGMA foreign_keys = ON"
            )  # 这是 SQLite 特有的配置，需要确保 SQLite 真正启用了外键约束
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        # 不自动提交；异常时兜底回滚，关闭时释放未提交的事务和连接。
        session = self._sessions()
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def init_db(db: Database) -> None:
    """应用启动入口：根据 ORM 模型创建缺失的表。"""

    db.init_tables()


# 当前进程共享同一个 Database / Engine，各请求仍创建独立 Session。
database = Database()
