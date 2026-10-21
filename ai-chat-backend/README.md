## 启动

```
# 创建虚拟环境
python -m venv .venv

# 安装依赖
python -m pip install -r requirements.txt

# 启动服务
uvicorn main:app --reload
```

访问 http://127.0.0.1:8000/
![1](./images/image1.png)
![2](./images/image2.png)

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
│
├── models/
│     schemas.py         ← Pydantic 模型
│
├── db/
│     orm_models.py      ← 数据库表定义
│     database.py        ← 数据库连接、Session 工厂和表初始化
│
├── core/
│     config.py          ← Pydantic Settings 配置
│     logger.py
│     paths.py           ← 统一管理项目路径
```

main.py 只负责：
- 创建 FastAPI 应用
- 数据库表初始化
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
|数据库 |SQLite + SQLAlchemy 2 ORM |Session 管理事务，ORM 初始化表结构 |
|流式传输 |SSE（StreamingResponse） |text/event-stream + 生成器 |
|配置 |python-dotenv |.env 注入密钥与开关 |

## 核心思路

数据库访问封装在 `MemoryStore`，`ChatService` 继续使用原有接口。`db/orm_models.py` 专门定义数据库表，`db/database.py` 管理连接、Session 工厂和表初始化。路由通过依赖注入获取请求级 Session，数据库层只负责创建、异常回滚和关闭，未提交的修改在关闭时丢弃；业务层显式调用 commit()，普通聊天在返回前提交整轮对话，流式聊天在发送 done 前提交，删除会话在返回前提交；会话删除通过数据库外键级联删除消息。db/database.py 定义进程级共享的 database 实例及 Engine，FastAPI lifespan 将其注册到 app.state.database，退出时释放 Engine；启动时调用 `init_db(database)`，通过 `Base.metadata.create_all()` 创建缺失的 ORM 表，保留已有表和数据。`create_all()` 不会修改已有表结构；后续字段变更需要手动更新数据库，或在确认开发数据可以丢弃后重建数据库，需要持续保留数据时可引入 Alembic 等迁移工具。

- `system prompt` 放在 prompts/system.md，不要写死在代码里。
- `多轮对话` 不应该由前端每次传一大堆历史，而是先由后端根据 conversation_id 管理。
- `聊天记录保存` 用 SQLite / PostgreSQL。
- `流式输出` 先在后端用 FastAPI 的 StreamingResponse，前端再用 fetch 读取 stream
- `React 前端` 只负责输入、展示、调用 /chat 或 /chat/stream，不要放 AI 逻辑。

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
