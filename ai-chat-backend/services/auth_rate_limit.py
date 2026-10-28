"""使用独立事务持久化认证请求计数，登录失败回滚时也不会丢失计数。"""

import math
from time import time
from typing import Literal

from core.config import settings
from db.database import Database
from db.orm_models import AuthRateLimit
from fastapi import HTTPException
from sqlalchemy import case, delete, or_, select
from sqlalchemy.dialects.sqlite import insert


def enforce_auth_rate_limit(
    database: Database, username: str, action: Literal["register", "login"]
) -> None:
    username = username.strip().lower()
    limit = getattr(settings, f"auth_{action}_rate_limit")
    window = getattr(settings, f"auth_{action}_rate_window_seconds")
    retry_after = None
    # 先写入再读取，SQLite 写事务负责串行化跨进程并发请求。
    with database.engine.begin() as connection:
        connection.execute(
            delete(AuthRateLimit).where(AuthRateLimit.expires_at <= time())
        )
        now = time()
        expired = AuthRateLimit.expires_at <= now
        expires_at = connection.execute(
            insert(AuthRateLimit)
            .values(
                action=action, username=username, attempts=1, expires_at=now + window
            )
            .on_conflict_do_update(
                index_elements=[AuthRateLimit.action, AuthRateLimit.username],
                set_={
                    "attempts": case((expired, 1), else_=AuthRateLimit.attempts + 1),
                    "expires_at": case(
                        (expired, now + window), else_=AuthRateLimit.expires_at
                    ),
                },
                where=or_(expired, AuthRateLimit.attempts < limit),
            )
            .returning(AuthRateLimit.expires_at)
        ).scalar_one_or_none()
        if expires_at is None:
            expires_at = connection.scalar(
                select(AuthRateLimit.expires_at).where(
                    AuthRateLimit.action == action, AuthRateLimit.username == username
                )
            )
            retry_after = max(1, math.ceil(expires_at - now))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="该账号请求过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
