"""集中处理 SQL 表达式的数据库方言差异。"""

from sqlalchemy import func
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.expression import Insert
from sqlalchemy.sql.functions import Function


def insert_statement(dialect_name: str) -> type[Insert]:
    """返回带 ON CONFLICT 支持的方言 insert 构造器。"""
    if dialect_name == "sqlite":
        return sqlite.insert
    if dialect_name == "postgresql":
        return postgresql.insert
    raise ValueError(f"不支持的数据库方言: {dialect_name}")


def now_expression(dialect_name: str) -> Function:
    """当前时间表达式。

    SQLite 沿用 datetime('now', 'localtime')，保持既有库里的时间写法不变；
    PostgreSQL 用事务时间戳，列类型是 timestamptz。
    """
    if dialect_name == "sqlite":
        return func.datetime("now", "localtime")
    if dialect_name == "postgresql":
        return func.now()
    raise ValueError(f"不支持的数据库方言: {dialect_name}")
