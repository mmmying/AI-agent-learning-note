# 负责数据库连接、Session 工厂和迁移入口
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.config import settings
from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from db.migrations import upgrade_database


def to_url(value: str | Path) -> str:
    """把 SQLite 文件路径或完整 SQLAlchemy 连接串统一成连接串。"""
    text = str(value)
    if "://" in text:
        return text
    return str(URL.create("sqlite+pysqlite", database=text))


class Database:
    """管理引擎和 Session 生命周期，事务由业务层显式提交。"""

    def __init__(self, url: str | Path | None = None) -> None:
        # 默认读取统一配置，显式 url 用于测试等场景。
        self.url = to_url(settings.sqlalchemy_url if url is None else url)
        parsed = make_url(self.url)
        self.is_sqlite = parsed.get_backend_name() == "sqlite"
        self.path = Path(parsed.database) if self.is_sqlite and parsed.database else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.is_sqlite:
            self.engine = create_engine(
                self.url,
                connect_args={
                    "check_same_thread": False
                },  # 这是 SQLite + Python 中比较常见的配置
                echo=settings.debug,
            )
            # 每当 Engine 建立一个新的数据库连接时，执行 _enable_foreign_keys
            event.listen(self.engine, "connect", self._enable_foreign_keys)
        else:
            # 网络数据库的连接可能已被服务端或中间件断开，检出前先探活。
            self.engine = create_engine(self.url, echo=settings.debug, pool_pre_ping=True)

        # sessionmaker 返回一个工厂，之后调用工厂就能创建新的 Session
        self._sessions = sessionmaker(bind=self.engine)

    def init_tables(self) -> None:
        """将数据库迁移到最新版本，保留现有初始化接口。"""
        upgrade_database(self.engine)

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
    """应用启动入口：执行 Alembic upgrade head。"""

    db.init_tables()


# 当前进程共享同一个 Database / Engine，各请求仍创建独立 Session。
database = Database()
