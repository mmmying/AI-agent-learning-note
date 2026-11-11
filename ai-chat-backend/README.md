## 启动

```
# 创建虚拟环境
python -m venv .venv

# 安装依赖
python -m pip install -r requirements.txt

# 可选：起一套本地 PostgreSQL、Redis 和打包好的后端（compose 文件在仓库根目录）
# 首次会 build 后端镜像；只想让后端跑在宿主机上热重载，就改成 up -d postgres redis
docker compose -f ../docker-compose.yml up -d

# 执行数据库迁移（应用启动时也会自动执行）
alembic upgrade head

# 启动服务
uvicorn main:app --reload
```

不配置 `DATABASE_URL` 时使用 `data/chat.db`（SQLite），仅适合本地开发和测试；生产用 PostgreSQL，认证限流计数始终放在 Redis，因此需要 `REDIS_URL` 可达。详见「生产数据库与共享状态」。

访问 http://127.0.0.1:8000/
![1](./images/image1.png)
![2](./images/image2.png)

## 自动化测试

测试使用 pytest，兼容原有 unittest 用例；使用 FastAPI TestClient、临时 SQLite 数据库和 fakeredis，无需启动服务、拉起 PostgreSQL/Redis 或配置 AI 密钥。

```powershell
# 安装运行和测试依赖（Python 3.11+）
python -m pip install -r requirements-dev.txt

# 执行全部测试 / 只执行数据库测试 / 只执行接口测试
python -m pytest
python -m pytest -m database
python -m pytest -m api

# 输出覆盖率、XML 和 JUnit 报告，供 CI 采集
python -m pytest --cov --cov-report=term-missing --cov-report=xml --junitxml=junit.xml

# Windows 一键执行，可指定独立测试环境
./scripts/test.ps1 -Python ./.venv/Scripts/python.exe -Coverage
```

`tests/conftest.py` 在导入应用前将 `DB_PATH` 指向临时目录，覆盖测试环境与 AI 密钥。`database` fixture 为每个用例分配独立数据库，并使用 Alembic 初始化。`redis_client` fixture 用 fakeredis 提供内存中的限流计数，跨进程共享场景用同一个 `FakeServer` 模拟。`client` fixture 执行真实的应用启动、迁移与关闭流程，AI 响应使用 mock，并阻止 HTTPX 发出真实网络请求。测试不会读写 `data/chat.db`，Engine 释放后清理临时文件。

测试覆盖普通/多轮聊天、请求校验、会话查询与删除、SSE 事件和落库、AI 异常、流式失败及提交失败回滚、SQLite 外键和非空约束、级联删除、Session 生命周期，以及新库/旧库迁移和结构一致性。`database` 标记包含数据库集成及接口落库测试。限流用例覆盖固定窗口的建立与重置、`Retry-After`、429 不延长窗口、422 不计次、无 TTL 孤儿键修复和多进程并发只放行额度内的请求。

PostgreSQL 方言测试默认跳过，需要一个真实测试库才执行；结构、外键、自增序列、时区和级联删除都在这一组里验证。这组用例会清空 `users`、`conversations`、`messages`，所以库名必须包含 `test`，指向别的库时用例直接失败而不是默默删数据：

```powershell
# 先 docker compose -f ../docker-compose.yml up -d postgres
docker compose -f ../docker-compose.yml exec postgres createdb -U ai_chat ai_chat_test
$env:TEST_DATABASE_URL = "postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat_test"
python -m pytest -m database tests/test_postgres_integration.py
```

测试失败时退出码非零；CI 可执行上述 pytest 报告命令。生成的报告和缓存已加入忽略规则。

测试临时文件统一放在项目的 `.test-tmp/run-*` 独立目录，结束后自动清理，避免 Windows 系统临时目录 `pytest-of-*` 的访问权限问题。也可通过 `--basetemp` 显式指定 pytest 临时目录；pytest 会清空该指定目录，请仅使用专门的测试目录。

## 项目结构

```
backend/
│
├── main.py              ← FastAPI 应用入口
│
├── api/
│     chat.py            ← 路由（Route）
│
├── services/
│     ai_client.py       ← 调用 OpenAI
│     chat_service.py    ← 负责 AI 对话的业务流程，属于业务编排层
|     memory_store.py    ← 负责对话历史的持久化存储和管理，属于数据持久化层
│     auth_rate_limit.py ← 基于 Redis 固定窗口的注册/登录限流
│
├── models/
│     schemas.py         ← Pydantic 模型
│
├── db/
│     orm_models.py      ← 数据库表定义
│     database.py        ← 数据库连接、Session 工厂和迁移入口
│     dialect.py         ← SQLite 与 PostgreSQL 的 SQL 方言差异
│     migrations.py      ← 使用应用 Engine 执行 Alembic 升级
│     redis_client.py    ← 共享状态（限流计数）的 Redis 连接
│
├── alembic.ini          ← Alembic 配置
├── migrations/
│     env.py             ← 迁移环境，复用应用配置和 ORM 元数据
│     versions/          ← 可追踪的数据库版本历史
│     script.py.mako     ← 生成迁移文件时使用的模板
│
├── scripts/
│     import_sqlite_to_postgres.py ← 一次性把 SQLite 数据搬进 PostgreSQL
│
├── core/
│     config.py          ← Pydantic Settings 配置
│     logger.py
│     paths.py           ← 统一管理项目路径
```

main.py 只负责：
- 创建 FastAPI 应用
- 启动时升级数据库版本
- 注册路由
- 做应用初始化（日志、配置、中间件等）

它只是**把整个应用组装起来**

## 整个调用链

```
浏览器
      │
      ▼
POST /chat
      │
      ▼
api/chat.py(Route)
      │
      ▼
ChatService(业务)
      │
      ▼
AIClient(OpenAI SDK)
      │
      ▼
OpenAI API
```

```
OpenAI API
      │
      ▼
AIClient
      │
      ▼
ChatService
      │
      ▼
Route
      │
      ▼
浏览器
```

chat_service 和 memory.store.py 调用关系：
```
  api 接口
     ↓
  ChatService
  chat()/chat_stream()
   ↓                ↓
MemoryStore       AIClient
保存/读取对话      调用大模型
会话管理           生成回复
   ↓                ↓
Database          LLM Provider
```
- ChatService 接收消息，组织上下文，调用AI，返回回复
- MemoryStore 保存消息，读取历史，管理会话
- AIClient 与大模型交互
- Database 提供数据库会话

## 技术栈
|领域 |选型 |说明 |
| --- | --- | --- |
|Web 框架 |FastAPI 0.139 + Uvicorn |ASGI，自带 OpenAPI 文档 |
|数据校验 |Pydantic v2 |请求/响应模型与类型契约 |
|AI SDK |openai 2.44 |通过 base_url 复用同一 SDK 调 DeepSeek |
|数据库 |PostgreSQL 18（生产）/ SQLite（本地、测试）+ SQLAlchemy 2 ORM + Alembic |同一套模型和迁移，由 `DATABASE_URL` 决定方言 |
|共享状态 |Redis 8 + redis 客户端 |认证限流计数，多进程和多实例共用 |
|流式传输 |SSE（StreamingResponse） |text/event-stream + 生成器 |
|配置 |python-dotenv |.env 注入密钥与开关 |

## 核心思路

数据库访问封装在 `MemoryStore`，`ChatService` 继续使用原有接口。`db/orm_models.py` 定义 ORM 模型，`db/database.py` 管理连接、Session 工厂和迁移入口。路由通过依赖注入获取请求级 Session，数据库层负责创建、异常回滚和关闭；业务层显式提交事务。普通聊天在返回前提交整轮对话，流式聊天在发送 done 前提交，删除会话在返回前提交；会话删除通过数据库外键级联删除消息。

`db/database.py` 按 `settings.sqlalchemy_url` 创建 Engine：未配置 `DATABASE_URL` 时是 SQLite 文件，配置后是 PostgreSQL（连接池加 `pool_pre_ping` 探活）。`Database` 实例进程级共享，FastAPI lifespan 将其注册到 `app.state.database`，同时创建 Redis 客户端注册到 `app.state.redis`，退出时分别释放 Engine 和连接池。启动时调用 `init_db(database)`，通过 Alembic 执行 `upgrade head`；版本记录存储在 `alembic_version` 表。迁移失败会阻止服务启动。应用不再调用 `create_all()`，修改模型后必须新增迁移文件并提交到版本控制。

SQLite 和 PostgreSQL 的 SQL 差异集中在 `db/dialect.py`：`ON CONFLICT DO NOTHING` 用各自的方言构造器，当前时间表达式在 SQLite 沿用 `datetime('now', 'localtime')`、在 PostgreSQL 用 `now()`（列类型 `timestamptz`）。业务层通过 `session.get_bind().dialect.name` 取方言名，不再直接 import 某个方言模块。

- `system prompt` 放在 prompts/system.md，不要写死在代码里。
- `多轮对话` 不应该由前端每次传一大堆历史，而是先由后端根据 conversation_id 管理。
- `聊天记录保存` 用 PostgreSQL（生产）或 SQLite（本地开发和测试），由 `DATABASE_URL` 决定。
- `流式输出` 先在后端用 FastAPI 的 StreamingResponse，前端再用 fetch 读取 stream
- `React 前端` 只负责输入、展示、调用 /chat 或 /chat/stream，不要放 AI 逻辑。

## 生产数据库与共享状态

生产环境的运行状态分两处：业务数据（用户、会话、消息）在 PostgreSQL，跨进程的认证限流计数在 Redis。SQLite 只保留给本地开发和测试，同一套 ORM 模型和 Alembic 迁移覆盖两种方言。

### 连接配置

| 变量 | 作用 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy 连接串 | 留空时回落到 `DB_PATH` 指向的 SQLite；生产填 `postgresql+psycopg://user:password@host:5432/dbname` |
| `DB_PATH` | SQLite 文件路径 | 仅 `DATABASE_URL` 为空时生效，默认 `data/chat.db` |
| `REDIS_URL` | Redis 连接串 | 默认 `redis://127.0.0.1:6379/0`，认证限流计数依赖它，不可达时接口返回 500 |

`APP_ENV=production` 时，`core/config.py` 会在应用启动前校验配置：`DATABASE_URL` 必须以 `postgresql` 开头，`JWT_SECRET_KEY` 必须显式配置，否则直接拒绝启动，避免生产误用 SQLite 单文件库或进程级随机密钥。

本地开发用仓库根目录的 `docker-compose.yml` 起一整套：PostgreSQL 18、Redis 8、打包好的后端镜像，以及编译成静态文件后由 nginx 托管的前端镜像。数据放在命名卷 `postgres-data` 里，端口都只绑 `127.0.0.1`，局域网访问不到：

```powershell
docker compose -f ../docker-compose.yml up -d                 # 四个服务，页面在 http://127.0.0.1:8080/
docker compose -f ../docker-compose.yml up -d postgres redis  # 只要数据服务，前后端都跑宿主机热重载
docker compose -f ../docker-compose.yml down                  # 保留数据卷
```

前端不直接连后端端口：浏览器只请求同源的 `/api/...`，由前端容器里的 nginx 反代到 compose 网络内部的 `backend:8000`，所以后端可以不发布到宿主机（见 `ai-chat-frontend/nginx.conf`）。同源的缘故，后端不需要配 CORS。反代时 nginx 会写 `X-Forwarded-For`，但 uvicorn 默认不解析它，限流计数记的是前端容器的地址，所有浏览器共用一个限流桶；要让每个真实客户端各自计数就给 uvicorn 加 `--proxy-headers --forwarded-allow-ips="*"`，前提是它真的只站在可信代理后面，否则客户端可以自己伪造这个头绕过限流。

后端容器用 compose 网络的服务名连数据库（`postgres:5432`、`redis:6379`），这两条在 compose 的 `environment` 里写死并覆盖 `.env`；AI 密钥仍从 `ai-chat-backend/.env` 读取（该文件被 gitignore，`.dockerignore` 也把它挡在镜像外），文件不存在时服务照样能起，只是 `/chat` 缺密钥不可用。

`.env` 里没有 `JWT_SECRET_KEY` 时，后端每次启动随机生成一个，容器重启后旧 token 全部失效；要跨重启保持登录就在 `.env` 里固定一个（`APP_ENV=production` 下不显式配置会直接拒绝启动）。

容器后端默认占宿主机 `127.0.0.1:8000`；这个端口已经被宿主机上的 uvicorn 占着时，用 `BACKEND_PORT` 换一个（PowerShell：`$env:BACKEND_PORT="8001"`），否则 `up -d backend` 会报 `ports are not available`。这个冲突只影响宿主机发布，前端容器走的 `backend:8000` 是 compose 内部地址，不受影响。`POSTGRES_PORT`、`REDIS_PORT`、`FRONTEND_PORT` 同理。`.env` 里的 `DEBUG=true` 会打开 SQLAlchemy 的 SQL echo，容器日志里全是每条 SQL，嫌吵就在 `.env` 里改成 `false`。

后端跑在宿主机时，`.env` 参考 `.env.example`：

```
DATABASE_URL=postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat
REDIS_URL=redis://127.0.0.1:6379/0
```

compose 里的 `ai_chat` 账号和口令仅供本地，生产请通过托管实例的密钥注入。构建后端镜像时容器里连 pypi.org 很慢（实测约 12 KiB/s，会 read timeout），换阿里云源能到约 150 KiB/s：

```powershell
$env:PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
docker compose -f ../docker-compose.yml build backend
```

清华源 `pypi.tuna.tsinghua.edu.cn` 对容器内请求返回 403，用不了；阿里云、中科大、腾讯云三个源实测可用。

### 从 SQLite 搬迁已有数据

`scripts/import_sqlite_to_postgres.py` 是一次性导入脚本，把现有 SQLite 运行库的用户、会话、消息搬进 PostgreSQL。先把目标库的连接串配好、停止服务并备份 SQLite 文件，再执行：

```powershell
python scripts/import_sqlite_to_postgres.py `
  --sqlite-path data/chat.db `
  --database-url "postgresql+psycopg://ai_chat:ai_chat@127.0.0.1:5432/ai_chat"
```

脚本的行为：

- 只读 SQLite，不修改源文件，可以反复重试。
- 导入前用应用自己的迁移把目标库升级到 head，保证结构一致。
- 目标库非空时拒绝执行，确认要合并才加 `--force`；写入在单个事务里完成，任何约束错误整体回滚，不留部分数据。
- 保留原始主键（会话 ID、消息自增 ID、用户 ID），导入后把 `messages.id` 的序列对齐到当前最大值，后续插入不会撞号。
- 旧库的时间戳来自两种基准：`users.created_at` 由 Python 的 UTC 时间写入，会话和消息时间由 SQL 的 `datetime('now', 'localtime')` 写入，都是不带时区的朴素值。脚本按列选择解释基准，统一换算成带时区的 UTC 存入 `timestamptz` 列，并打印时间范围供人工核对是否整体偏移。
- 完成后按主键顺序逐行比对源库和目标库，而不只是数行数。
- 限流计数不迁移，切换后 Redis 从 0 重新累计。

脚本适合个人聊天库这种规模（全表读进内存校验），不适合需要长时间在线的大表迁移。

### Redis 存什么

目前只存认证限流计数，键为 `auth:rate-limit:<register|login>:<规范化用户名>`，值是窗口内的请求次数，TTL 就是剩余窗口时间。固定窗口语义与之前一致：首次请求用 `SET NX EX` 建键并开启窗口，后续请求 `INCR`，超限返回 429 和 `Retry-After`，超限本身不延长窗口；进程崩溃留下的无 TTL 键会在下次请求补上过期时间。

Redis 不可达时请求会抛连接错误，不会静默降级成不限流。客户端是惰性连接，启动时不探测 Redis，直到第一次注册或登录请求才暴露问题。计数不落数据库，因此重启服务不影响计数，重启 Redis（compose 默认关闭持久化）则计数清零。

## 数据库迁移

以下命令在项目根目录、使用项目虚拟环境执行。CLI 和应用共用 `core.config.settings.sqlalchemy_url`：配置了 `DATABASE_URL` 时连 PostgreSQL，否则连 `DB_PATH` 指向的 SQLite，默认是 `data/chat.db`。SQLite 相对路径相对于当前工作目录。迁移目录通过 `alembic.ini` 的位置定位。

```powershell
# 查看当前版本和迁移历史
python -m alembic current
python -m alembic history

# 修改 db/orm_models.py 后生成迁移
python -m alembic revision --autogenerate -m "add conversation field"

# 检查生成的 upgrade/downgrade 后应用迁移
python -m alembic upgrade head

# 检查数据库与 ORM 模型是否一致
python -m alembic check

# 回退一个版本；请先停服务并备份数据库
python -m alembic downgrade -1
```

`0001` 是初始迁移：空数据库直接建表；项目早期手写 SQL 创建的数据库会先检查表结构，再复制到与 ORM 一致的新表，保留会话、消息 ID、时间值、自增序号和 `schema_migrations` 历史表。复制和替换在同一事务中执行，外键始终开启。未知结构、额外字段或触发器会阻止自动接管，需为实际结构编写迁移。首次升级旧库前应停止服务并备份 SQLite 文件；测试在临时数据库和副本上执行。

如果数据库已经由旧版 `Base.metadata.create_all()` 建成，且确认字段、约束和索引与 `migrations/versions/0001_initial_schema.py` **完全一致**，备份后可用 `python -m alembic stamp 0001` 登记初始版本，再执行 `python -m alembic upgrade head` 和 `python -m alembic check`。`stamp` 只写版本号，不修改结构，不能用于跳过结构差异。

迁移环境按连接串判断方言：只有 SQLite 开启 `render_as_batch=True`，自动生成的字段变更用 batch 模式重建表；PostgreSQL 直接生成 `ALTER TABLE`，不需要 batch。`0004` 把认证限流表 `auth_rate_limits` 删掉，计数改由 Redis 保存；回退到 `0003` 会重建空表，但已经丢失的历史计数无法恢复。自动生成不能可靠识别重命名，必须人工检查；涉及被引用的父表重建时，还需安排外键和数据复制顺序。[Alembic SQLite batch 文档](https://alembic.sqlalchemy.org/en/latest/batch.html)

时间列统一用 `DateTime(timezone=True)`：SQLite 仍存文本，PostgreSQL 是 `TIMESTAMP WITH TIME ZONE`，读写都带时区。SQLite 的时间默认值来自 SQL 的 `datetime('now', 'localtime')`，PostgreSQL 用 `now()`，两者都由 `db/dialect.py` 提供。新加字段建议留可空列或带 `server_default`，避免 PostgreSQL 对已有数据的表直接加非空约束失败。

`downgrade base` 会删除用户、会话和消息表。应用启动会重新升级到 head，因此回退数据库时应同时部署匹配版本的代码。

## 调用 DeepSeek API

DeepSeek API 使用与 OpenAI/Anthropic 兼容的 API 格式，通过修改配置，可以使用 OpenAI/Anthropic SDK 来访问 DeepSeek API

```python
# Please install OpenAI SDK first: `pip3 install openai`
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ],
    stream=False,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}}
)

print(response.choices[0].message.content)
```

https://api-docs.deepseek.com/zh-cn/

## 流式输出

流式输出的核心思想：生成器不断 yield 新的数据，StreamingResponse 每得到一个值就立即发送给客户端

## 联网搜索

前端输入框工具栏有「联网搜索」开关（偏好存 localStorage，默认关闭）。开启后请求体带 `web_search: true`，后端先调用模型把用户消息**改写**成聚焦的搜索主题词（覆盖「我不懂…」「介绍下…」「…是什么」等任意口语外壳；词表剥离只作为改写失败/输出为空时的回退），再实时搜索网页，把标题/链接/摘要注入本轮系统提示词并要求模型用 Markdown 链接标注来源；搜索结果不写入会话记忆，历史只保留用户消息与助手回复。实际使用的搜索词会通过 SSE 事件返回并在前端「搜索词」标签中展示；来源区块展示在回答末尾，并随助手消息落库（`messages.sources` / `messages.search_query`），切换会话后历史对话仍保留参考来源。

比较/多实体问题（如「A 和 B 什么区别」）会**拆分搜索再合并来源**：改写让模型按实体输出多条查询词（`|` 分隔，最多 3 条），逐个实体分别搜索，再按 URL 去重、轮转合并（上限 `SEARCH_MAX_RESULTS * 2`），保证每个实体都有代表性来源；某个实体搜索失败不影响其余。这是因为 Bing 对「A和B区别」这类组合查询召回极差（退化成泛泛公司页），而单独搜每个实体都很准。改写失败时回退到启发式拆分（按「和/与/、/vs」等连接词切分并剥掉「什么区别/对比」等结尾）。拼接后的搜索词（`A / B`）沿用同一 SSE 字段与落库列，前端无需改动。

Bing 对裸品牌词偶发返回**无关噪声 SERP**（如京东/ChatGPT 首页，与查询词毫无关系）。逐实体搜索后会做相关性门控：若结果无一在标题/链接/摘要中提及查询词，判定为噪声，用精确匹配（`"实体"`）重试一次；重试仍无关则丢弃该实体，绝不把垃圾来源注入提示词误导模型。全部实体都无可靠结果时按「未找到相关搜索结果」降级。

搜索后端由 `SEARCH_PROVIDER` 决定：`auto`（默认）在配置了 `TAVILY_API_KEY` 时用 Tavily，否则用免密钥的 Bing 网页搜索（解析 HTML 并解码跳转链接）；也可显式指定 `tavily` / `bing`。`SEARCH_MAX_RESULTS`、`SEARCH_TIMEOUT` 控制条数与超时。搜索默认直连（`SEARCH_TRUST_ENV=false`），因为实测部分系统代理会给 Bing 返回缓存的无关结果；必须经代理出网时改为 `true`。

流式接口在 `start` 之后、`delta` 之前额外推送 `search_start`、`search_result`（含 `results`）或 `search_error` 事件，前端据此显示「正在联网搜索」、来源 chips 或降级提示。搜索失败或无结果时不阻塞聊天：推送 `search_error` 后按普通模式继续回复。非流式 `/chat/chat` 的响应里用 `sources` 字段返回来源。

## JWT 用户认证与多用户聊天

先安装最新 `requirements.txt`，再启动后端和 `ai-chat-frontend`（前端执行 `npm install`、`npm run dev`）。前端通过 Vite 的 `/api` 代理访问后端；生产部署也需要将 `/api/*` 转发到后端并去掉 `/api` 前缀。

前端支持注册、登录、退出和页面刷新后恢复登录状态。用户名为 3–32 位字母、数字或下划线，自动转成小写；密码为 8–128 位字符，使用 Argon2 哈希保存。注册成功会直接登录。

| 接口 | 请求 / 用途 |
| --- | --- |
| `POST /auth/register` | JSON `{"username":"alice","password":"your-password"}`，注册并返回令牌与用户信息 |
| `POST /auth/login` | 相同 JSON 格式，登录并返回令牌与用户信息 |
| `GET /auth/me` | 查询当前用户 |

除注册、登录和健康检查外，聊天和会话接口均要求 `Authorization: Bearer <access_token>`。JWT 使用 HS256，校验签名、有效期、签发者和受众；默认有效期为 60 分钟，通过 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` 配置。前端将令牌存于 `sessionStorage`，关闭标签页后清除；退出会清除本地令牌和聊天状态，中止正在生成的回复。令牌过期或无效时返回登录页。本实现没有刷新令牌和服务器端注销列表，已签发的令牌到期前仍有效。

在 `.env` 中设置 `JWT_SECRET_KEY`（至少 32 字符），可执行以下命令生成：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

开发环境未设置密钥时，每个进程生成随机密钥，重启后须重新登录；多进程或多实例部署必须使用相同的持久密钥。`APP_ENV=production` 时缺少密钥会拒绝启动。生产部署应通过 HTTPS 访问。

启动时自动应用迁移 `0002`，增加 `users` 表及 `conversations.user_id` 外键与索引。升级前请先停止服务并备份数据库。已有会话和消息完整保留，用户归属为 NULL，不会自动分配给任何新账号；如需认领，需由管理员核实归属后单独迁移。

会话列表、消息读取、继续聊天和删除都按当前用户过滤。访问其他用户、未分配或不存在的会话统一返回 404。新聊天应不传 `conversation_id`（或传 NULL），由服务端生成 ID；传入 ID 表示继续已有的本人会话。流式接口在发送 SSE 响应前检查权限。AI 服务商的密钥错误返回 502，避免与用户 JWT 的 401 混淆。

认证实现参考 [FastAPI 官方 JWT 与密码哈希文档](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)。

注册和登录按规范化后的用户名独立限流：默认每 60 秒最多注册 3 次、登录 5 次，成功、用户名重复和密码错误均计入次数；参数校验失败的 422 请求不计入。窗口从该账号对应操作的首次请求开始，过期后重置；超限请求返回 429 和以秒为单位的 `Retry-After` 响应头，不延长窗口。不同账号、注册与登录之间互不占用额度，当前用户查询不受此限制。

可在 `.env` 中调整 `AUTH_REGISTER_RATE_LIMIT`、`AUTH_REGISTER_RATE_WINDOW_SECONDS`、`AUTH_LOGIN_RATE_LIMIT`、`AUTH_LOGIN_RATE_WINDOW_SECONDS`，修改后重启服务。计数保存在 Redis 的 `auth:rate-limit:<action>:<用户名>` 键里，键的 TTL 就是窗口剩余时间：登录失败或注册失败不会回滚计数，多进程和多实例天然共用同一份额度，窗口结束后键自动过期，不需要清理任务。迁移 `0004` 在启动时删除原先存放计数的 `auth_rate_limits` 表，不修改已有用户与会话；Redis 重启后计数从 0 重新累计。这里只按账号限流，不包含按 IP 限制。

用户名去除首尾空格并转为小写后，注册会先检查唯一性，已存在时返回 409 并跳过密码哈希；数据库唯一约束继续保护并发注册，只有用户名冲突映射为 409，其他数据库约束错误不再误报为用户名重复。
