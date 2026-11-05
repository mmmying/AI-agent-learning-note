# syntax=docker/dockerfile:1
# 后端生产镜像。依赖单独一层，改代码不会重装依赖；容器以非 root 运行。
#
#   docker build -t ai-chat-backend ./ai-chat-backend
#   docker build -t ai-chat-backend --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple ./ai-chat-backend
#   docker compose -f ../docker-compose.yml up -d postgres redis
#   docker run --rm -p 8000:8000 \
#     -e APP_ENV=production \
#     -e DATABASE_URL="postgresql+psycopg://ai_chat:ai_chat@host.docker.internal:5432/ai_chat" \
#     -e REDIS_URL=redis://host.docker.internal:6379/0 \
#     -e JWT_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
#     -e DEEPSEEK_API_KEY=... \
#     ai-chat-backend
#
# 配置只从环境变量读取（见 .env.example），.env 和 data/ 已被 .dockerignore 排除在镜像之外。
# 数据库迁移由应用启动时的 lifespan 自动执行（db/migrations.py 的 alembic upgrade head），
# 因此镜像不需要额外的迁移步骤或入口脚本。

# --- 依赖层：只用 venv 收集依赖，不进最终镜像的构建工具链 ---
FROM python:3.12-slim AS deps

# 默认走官方 PyPI；容器里下载太慢就用 --build-arg 换成镜像源，例如
#   --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
# 清华源（pypi.tuna.tsinghua.edu.cn）对容器内的请求返回 403，实测不可用。
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt

# --- 运行层 ---
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --no-create-home app

WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY . .

# 未配置 DATABASE_URL 时应用会退回 data/chat.db（仅本地/测试用法），需要可写目录。
# 生产环境配了 PostgreSQL，这个目录始终是空的。
RUN mkdir -p /app/data && chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as r; r.urlopen('http://127.0.0.1:8000/', timeout=3)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
