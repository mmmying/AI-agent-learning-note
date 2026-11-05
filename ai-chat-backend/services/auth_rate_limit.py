"""认证请求频率限制：计数保存在 Redis，多进程和多实例共用同一份状态。"""

import math
from typing import Literal

import redis
from core.config import settings
from fastapi import HTTPException


def enforce_auth_rate_limit(
    client: redis.Redis, username: str, action: Literal["register", "login"]
) -> None:
    """按固定窗口计数，窗口从该账号该操作的首次请求开始，到期后重置。

    键只写入 Redis 的短生命周期计数，不含业务数据；超限请求返回 429 和
    剩余秒数，且不会延长窗口。
    """
    key = f"auth:rate-limit:{action}:{username.strip().lower()}"
    limit = getattr(settings, f"auth_{action}_rate_limit")
    window = getattr(settings, f"auth_{action}_rate_window_seconds")
    # SET NX 原子开启窗口，已有键则累加；两条命令都是原子的，无需事务。
    try:
        if client.set(key, "1", nx=True, ex=window):
            attempts = 1
        else:
            attempts = int(client.incr(key))
            if client.pttl(key) < 0:
                # 进程在 INCR 建键后崩溃会留下没有过期时间的键，这里补上窗口。
                client.expire(key, window)
    except redis.exceptions.RedisError as exc:
        # Redis 不可用时拒绝认证而非放行，否则限流在故障期间形同虚设。
        raise HTTPException(
            status_code=503,
            detail="认证服务暂时不可用：限流存储 Redis 连接失败，请确认 Redis 已启动",
            headers={"Retry-After": "5"},
        ) from exc
    if attempts > limit:
        retry_after = max(1, math.ceil(client.pttl(key) / 1000))
        raise HTTPException(
            status_code=429,
            detail="该账号请求过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
