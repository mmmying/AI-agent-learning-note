import secrets
from pathlib import Path
from typing import Literal

from core.paths import DB_PATH, ENV_FILE
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # DeepSeek 思考模式开关与强度
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    deepseek_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "high"

    db_path: Path = DB_PATH
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
    def _production_secret(self):
        if (
            self.app_env == "production"
            and "jwt_secret_key" not in self.model_fields_set
        ):
            raise ValueError("production 环境必须配置 JWT_SECRET_KEY（至少 32 字符）")
        return self

    # 日志级别
    log_level: LogLevel = "INFO"

    @field_validator(
        "ai_provider",
        "deepseek_thinking",
        "deepseek_reasoning_effort",
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
