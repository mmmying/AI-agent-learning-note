"""供 FastAPI 路由使用的数据库依赖。"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from db.database import Database


def get_database(request: Request) -> Database:
    """获取 lifespan 注册的共享 Database，复用引擎和 Session 工厂。"""
    # 这里返回 db/database.py 中的全局实例，不会为每次请求创建 Database。
    return request.app.state.database


# Annotated 保留 Database 类型信息，Depends 告诉 FastAPI 调用哪个依赖函数。
DatabaseDep = Annotated[Database, Depends(get_database)]


def get_db(database: DatabaseDep) -> Iterator[Session]:
    """每次请求创建独立 Session，异常回滚、结束时关闭，业务层负责提交。"""
    # 依赖链：路由 -> get_db -> get_database -> 应用共享的 database。
    # 保留注入参数，而非直接引用全局实例，便于测试时替换数据库：
    # app.dependency_overrides[get_database] = lambda: test_database
    # FastAPI 会将 test_database 注入此参数，避免测试读写真实数据库。
    # overrides 只影响 FastAPI 解析依赖，普通 Python 函数调用不会触发替换。
    with database.session() as session:
        # yield 前创建 Session；请求处理（含流式响应）结束后退出上下文并清理。
        # commit() 由业务层显式调用，依赖层不自动提交。
        yield session


# 路由参数可以直接声明为 db: SessionDep。
# request 作用域确保流式响应迭代结束后才关闭 Session。
SessionDep = Annotated[Session, Depends(get_db, scope="request")]
