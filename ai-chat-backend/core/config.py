import secrets
from pathlib import Path
from typing import Literal

from core.paths import DB_PATH, ENV_FILE
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import URL

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """应用配置：取值优先级为环境变量 > .env 文件 > 这里的默认值，启动时校验类型与取值范围"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,  # 自动加载 .env 文件
        env_file_encoding="utf-8",  # 文件编码
        case_sensitive=False,  # 不区分大小写
        extra="ignore",  # 忽略 .env 中多出来的键，避免报错
    )

    # 应用信息，会显示在 FastAPI 自动生成的文档标题上
    app_name: str = "AI Chat Backend"
    app_env: Literal["local", "development", "test", "production"] = "local"

    # AI 服务商与密钥
    ai_provider: Literal["openai", "deepseek"] = "deepseek"
    openai_api_key: str = ""
    deepseek_api_key: str = ""

    # 模型与请求参数
    openai_model_name: str = "gpt-5"
    deepseek_model_name: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    timeout: int = 30

    # 上下文最大 token 预算
    context_max_tokens: int = Field(default=16384, ge=4096)
    # 预留输出预算
    context_response_reserve: int = Field(default=4096, ge=256)
    # 摘要预算
    context_summary_max_tokens: int = Field(default=1024, ge=128)
    context_recent_turns: int = Field(default=6, ge=0)

    @model_validator(mode="after")
    def _context_budget(self):
        if self.context_summary_max_tokens > self.context_response_reserve:
            raise ValueError(
                "CONTEXT_SUMMARY_MAX_TOKENS 不能超过 CONTEXT_RESPONSE_RESERVE"
            )
        input_budget = self.context_max_tokens - self.context_response_reserve
        if input_budget < 2 * self.context_summary_max_tokens + 2048:
            raise ValueError("上下文输入预算需容纳摘要、摘要指令和最近消息")
        return self

    # DeepSeek 思考模式开关与强度
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    deepseek_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "high"

    # 联网搜索：auto 表示配置了 TAVILY_API_KEY 就用 Tavily，否则退回免密钥的 Bing 网页搜索
    search_provider: Literal["auto", "tavily", "bing"] = "auto"
    tavily_api_key: str = ""
    search_max_results: int = Field(default=5, ge=1, le=10)
    search_timeout: int = Field(default=10, ge=1, le=60)
    # 搜索默认直连（不走环境变量/系统代理）：实测某些系统代理会给 Bing 返回缓存的无关结果。
    # 若所在网络必须经代理才能出网，设为 true。
    search_trust_env: bool = False

    db_path: Path = DB_PATH
    # 生产用 PostgreSQL 连接串（如 postgresql+psycopg://user:pwd@host:5432/db），
    # 留空则退回 db_path 上的 SQLite，仅用于本地开发和测试。
    database_url: str = ""
    # 限流计数等跨进程共享状态存放的 Redis。
    redis_url: str = "redis://127.0.0.1:6379/0"
    debug: bool = True
    jwt_secret_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(48), min_length=32, repr=False
    )
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1, le=10080)
    auth_register_rate_limit: int = Field(default=3, ge=1, le=10000)
    auth_login_rate_limit: int = Field(default=5, ge=1, le=10000)
    auth_register_rate_window_seconds: int = Field(default=60, ge=1, le=86400)
    auth_login_rate_window_seconds: int = Field(default=60, ge=1, le=86400)

    @model_validator(mode="after")
    def _production_requirements(self):
        if self.app_env != "production":
            return self
        if "jwt_secret_key" not in self.model_fields_set:
            raise ValueError("production 环境必须配置 JWT_SECRET_KEY（至少 32 字符）")
        if not self.database_url.startswith("postgresql"):
            raise ValueError(
                "production 环境必须配置 PostgreSQL 的 DATABASE_URL，"
                "SQLite 只用于本地开发和测试"
            )
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 连接串：显式 DATABASE_URL 优先，否则用 db_path 的 SQLite。"""
        if self.database_url:
            return self.database_url
        return str(URL.create("sqlite+pysqlite", database=str(self.db_path)))

    # 日志级别
    log_level: LogLevel = "INFO"

    @field_validator(
        "ai_provider",
        "deepseek_thinking",
        "deepseek_reasoning_effort",
        "search_provider",
        mode="before",
    )
    @classmethod
    def _to_lower(cls, value: object) -> object:
        # 兼容 .env 中写成大写或混合大小写的取值
        return value.lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _to_upper(cls, value: object) -> object:
        # 日志级别名在 logging 中是大写的
        return value.upper() if isinstance(value, str) else value


# 全局唯一配置实例，其他模块统一 from core.config import settings
settings = Settings()
