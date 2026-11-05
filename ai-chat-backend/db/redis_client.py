"""跨进程共享状态的连接入口。

限流计数这类需要多进程、多实例共用的短期状态放在 Redis，
不进业务数据库，避免每次认证请求都参与主库事务。
"""

import redis


def create_redis_client(url: str) -> redis.Redis:
    """创建同步客户端；应用生命周期内共用同一个连接池。

    socket 超时保证 Redis 不可用时认证请求快速失败，而不是无限阻塞。
    """
    return redis.Redis.from_url(
        url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5
    )
