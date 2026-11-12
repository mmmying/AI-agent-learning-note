# Token 用量统计实现方案

> 适用仓库：`ai-chat-backend`（FastAPI）+ `ai-chat-frontend`（React / Vite）。
> 本文覆盖「会话累计 token 用量」的完整实现链路：捕获 → 传播 → 消费 → 原子累加落库 → API 暴露 → 前端展示。
> 文中代码索引均为当前仓库真实存在的文件与符号，行号以当前 `main` 为准。
> 配套阅读：`docs/stream_output_example.md`（一次真实流式调用的收尾事件序列与 usage 拆解）。

---

## 1. 目标与边界

**目标**：统计每个会话累计消耗的 token（输入 / 输出 / 总量），每轮对话结束后累加落库，并在前端顶栏以「本对话 N tokens」展示，悬停可见输入 / 输出明细。

**边界（刻意不做的事）**：

- 统计粒度是**会话累计**，不是单条消息；不新建 per-message 用量表，只在 `conversations` 上加三列累计值。
- 只持久化 `input_tokens / output_tokens / total_tokens` 三个量；`cached_tokens`、`reasoning_tokens` 不单独落库（语义见 §3.3）。
- 用量来自模型厂商返回的 `usage`，**不自行估算 / 分词计数**，避免与计费口径不一致。
- 统计是**旁路增强**：上游没返回 usage（中断 / 异常）按 0 计，绝不阻塞回复。
- 一轮对话（用户消息 + 助手消息 + 用量累加）是**一个事务**，失败整体回滚，不为失败的轮次计数。

---

## 2. 总体架构

```
模型厂商 Responses API
   └─ response.completed 事件携带 usage（流式唯一来源）
        ▼
services/strategy.py  OpenAICompatibleStrategy
   ├─ chat()        → (output_text, TokenUsage)            非流式：直接读 response.usage
   └─ chat_stream() → yield delta… ; return TokenUsage     流式：生成器返回值携带用量
        ▼
services/ai_client.py  AIClient
   ├─ chat()        → 透传元组
   └─ chat_stream() → usage = yield from strategy… ; return usage
        ▼
services/chat_service.py  ChatService
   ├─ 非流式：reply, usage = ai_client.chat(...)
   ├─ 流式：  next(stream) 逐段包成 SSE delta；StopIteration.value 即 usage
   ├─ memory_store.add_token_usage(conversation_id, usage)   原子累加
   ├─ session.commit()                                       一轮一事务
   └─ 非流式 ChatResponse.usage / 流式 SSE done.usage
        ▼
services/memory_store.py + db/orm_models.py
   └─ UPDATE conversations SET col = col + :delta WHERE id AND user_id
        ▼
API 暴露：ChatResponse.usage / SSE done.usage / ConversationSummary.usage
        ▼
前端 App.tsx：每轮结束 loadConversations() 刷新侧栏
   └─ activeUsage = 列表里当前会话的 usage → 顶栏 token-tag（总量 + 悬停明细）
```

关键文件：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 模型 | `models/schemas.py` | `TokenUsage`；`ChatResponse.usage`、`ConversationSummary.usage` |
| 捕获 | `services/strategy.py` | 从 `response.usage` / `response.completed` 提取用量；`_extract_usage` 兜底 |
| 传播 | `services/ai_client.py` | `yield from` 透传增量并捕获生成器返回值 |
| 消费 | `services/chat_service.py` | 非流式解包元组；流式 `next()`/`StopIteration`；累加 + 提交 + 暴露 |
| 存储 | `services/memory_store.py`、`db/orm_models.py` | 原子累加三列；列表读回 `usage` |
| 迁移 | `migrations/versions/0005_conversation_token_usage.py` | `conversations` 增三列，`server_default="0"` |
| 事务 | `db/database.py`、`db/dependencies.py` | 请求级 Session，异常回滚，业务层提交 |
| 路由 | `api/chat.py`、`api/conversations.py` | `/chat/chat`、`/chat/stream`、`/conversations` |
| 前端 | `src/api/chat.ts`、`src/App.tsx`、`src/App.css`、`src/Icons.tsx` | 类型、派生 `activeUsage`、顶栏胶囊、图标 |

---

## 3. 数据来源：Responses API 的 usage

### 3.1 非流式

`client.responses.create(...)` 返回的 `Response` 对象直接带 `usage`，与 `output_text` 一并读取（`strategy.py:85`）。

### 3.2 流式：只有 `response.completed` 带 usage

一次真实调用的收尾事件序列（完整 dump 见 `stream_output_example.md`）：

| seq | type | 关键 payload | 代码处理 |
| --- | --- | --- | --- |
| …597/598 | `response.output_text.delta` | `delta` 增量碎片 | `yield` 给 SSE（`strategy.py:105-106`） |
| 599 | `response.output_text.done` | `text=` 该 part 完整拼接 | 忽略（增量已累加） |
| 600 | `response.content_part.done` | 关闭 content part | 忽略 |
| 601 | `response.output_item.done` | 关闭整条输出消息 | 忽略 |
| 602 | `response.completed` | `response=Response(...)`，**唯一带 usage** | 捕获 usage（`strategy.py:107-108`） |

要点：`*.done` 事件虽然携带完整文本，但**都不带 usage**；用量只在最后的 `response.completed` 里。因此流式必须迭代到该事件才能拿到本轮用量。

### 3.3 `ResponseUsage` 字段语义

真实样例（`deepseek-flash`，思考关闭）：

```python
usage = ResponseUsage(
    input_tokens=53,                                            # prompt：system + 历史 + 本轮提问
    input_tokens_details=InputTokensDetails(cached_tokens=0),   # 无 prompt 缓存命中
    output_tokens=595,                                          # 生成的回答
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),  # 思考关闭，无推理 token
    total_tokens=648,                                           # = 53 + 595，自洽
)
```

- `total = input + output`（648 = 53 + 595）。
- `reasoning_tokens` 已**计入 `output_tokens` 内部**，开启思考时 `>0`，但不会额外加到 `total`；故只存 output 即可，无需单列。
- `cached_tokens` 是 `input_tokens` 的**子集**（命中缓存的部分），不是额外增量；当前不单独持久化。

`TokenUsage` 只取这三个量（`schemas.py:48-52`），缺省 0，兼容未统计的历史数据。

---

## 4. 捕获层：strategy

`OpenAICompatibleStrategy` 对 OpenAI / DeepSeek 统一走 Responses API。

**非流式**（`strategy.py:71-85`）：返回元组，文本与用量一次取齐。

```python
return response.output_text, self._extract_usage(response.usage)
```

**流式**（`strategy.py:87-109`）：声明为 `Generator[str, None, TokenUsage]`——产出 `str` 增量，**返回值**是 `TokenUsage`。

```python
usage = TokenUsage()
for event in stream:
    if event.type == "response.output_text.delta":
        yield event.delta
    elif event.type == "response.completed":
        usage = self._extract_usage(event.response.usage)
return usage
```

**兜底**（`strategy.py:111-120`）：上游未返回 usage（中断 / 异常）时按 0 计，不阻塞回复。

```python
@staticmethod
def _extract_usage(usage: ResponseUsage | None) -> TokenUsage:
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )
```

---

## 5. 传播层：AIClient（为什么用生成器返回值）

`AIClient.chat_stream` 用 `yield from` 把子生成器的增量透明转发，同时**捕获其返回值**再返回（`ai_client.py:32-42`）：

```python
usage = yield from self.strategy.chat_stream(system_prompt, messages)
return usage
```

设计取舍：

- 流式接口既要**逐段产出增量**（供 SSE），又要在结束时**带回一个用量值**。Python 生成器的 `return value` 会以 `StopIteration.value` 暴露，`yield from` 恰好能接住它——这是语言原生、无副作用的通道。
- **不采用**实例属性 / 可变 out-param / 全局状态：`AIClient`、strategy 在请求间可能复用，隐藏状态会引入并发串扰；生成器返回值是**请求内局部**的，天然隔离。
- 用量随生成器生命周期自然结束，无需额外清理。

---

## 6. 消费层：ChatService

### 6.1 非流式（`chat_service.py:78-96`）

```python
reply, usage = ai_client.chat(system_prompt=system_prompt, messages=messages)
self.memory_store.add_message(conversation_id, Message(role="assistant", content=reply, ...))
self.memory_store.add_token_usage(conversation_id, usage)
self.memory_store.session.commit()          # 一轮一事务，成功后再返回
return ChatResponse(conversation_id=conversation_id, reply=reply, usage=usage, sources=sources)
```

### 6.2 流式（`chat_service.py:139-170`）

不能用 `yield from`，因为每个增量都要**包成 SSE 事件**再产出；改为手动 `next()` 循环，用 `StopIteration.value` 取用量：

```python
reply_parts: list[str] = []
usage = TokenUsage()
stream = ai_client.chat_stream(system_prompt=system_prompt, messages=messages)
while True:
    try:
        delta = next(stream)
    except StopIteration as stop:
        usage = stop.value or TokenUsage()   # 生成器返回值即本轮用量
        break
    reply_parts.append(delta)
    yield self._sse_event({"type": "delta", "content": delta})

reply = "".join(reply_parts)
self.memory_store.add_message(conversation_id, Message(role="assistant", content=reply, ...))
self.memory_store.add_token_usage(conversation_id, usage)
self.memory_store.session.commit()           # 提交失败不发 done，由 Session 依赖回滚
yield self._sse_event({"type": "done", "conversation_id": conversation_id, "usage": usage.model_dump()})
```

`stop.value or TokenUsage()` 双保险：即便上游返回 `None` 也退化为 0。

---

## 7. 累加落库：原子 UPDATE + 多用户隔离

`MemoryStore.add_token_usage`（`memory_store.py:137-148`）：

```python
def add_token_usage(self, conversation_id: str, usage: TokenUsage) -> None:
    self.session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == self.user_id)
        .values(
            input_tokens=Conversation.input_tokens + usage.input_tokens,
            output_tokens=Conversation.output_tokens + usage.output_tokens,
            total_tokens=Conversation.total_tokens + usage.total_tokens,
        )
    )
```

- **原子累加**：`SET col = col + :delta` 在数据库内完成，**不是** Python 侧「读-改-写」。同一会话并发多轮也不会丢失更新（无竞态覆盖）。
- **多用户隔离**：`WHERE` 带 `user_id` 谓词，绝不累加到他人或未分配的会话。
- **同事务**：与助手消息写入共用一个 Session，由 `ChatService` 在轮末统一 `commit()`；存储层不提交、不关闭 Session（`memory_store.py:15-17`）。
- 列表读回：`list_conversations` 把三列组装成 `ConversationSummary.usage`（`memory_store.py:39-43`）。

---

## 8. 数据库设计与迁移 0005

`Conversation` 增三列（`orm_models.py:48-57`），`NOT NULL` + `default=0` + `server_default=text("0")`：

```python
input_tokens:  Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
total_tokens:  Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
```

迁移 `0005_conversation_token_usage.py`（`revision="0005"`，`down_revision="0004"`）：

```python
_COLUMNS = ("input_tokens", "output_tokens", "total_tokens")

def upgrade():
    for column in _COLUMNS:
        op.add_column("conversations",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"))

def downgrade():
    for column in reversed(_COLUMNS):
        op.drop_column("conversations", column)
```

- `server_default="0"` 让**存量历史会话**平滑回填 0，无需数据迁移脚本，也不会因 `NOT NULL` 失败。
- ORM 与 DB **零漂移**：`test_migrations.py:42-47` 断言 `upgrade head` 后 `compare_metadata(...) == []`。
- 迁移链：token 用量是 `0005`；其后联网搜索新增 `0006_message_search_sources`，**当前 head = 0006**（`test_migrations.py:44`、`test_postgres_integration.py:53`）。0005 的三列已包含在 head 的零漂移校验内。

---

## 9. API 契约

用量经**三条通道**暴露，口径一致（均为 `TokenUsage` 三字段）：

| 通道 | 位置 | 形态 |
| --- | --- | --- |
| 非流式响应体 | `POST /chat/chat` → `ChatResponse.usage`（`schemas.py:58`） | 本轮用量 |
| 流式收尾事件 | `POST /chat/stream` → SSE `done.usage`（`chat_service.py:164-170`） | 本轮用量 |
| 会话列表 | `GET /conversations` → `ConversationSummary.usage`（`schemas.py:76`） | 会话累计用量 |

SSE `done` 事件示例：

```json
{"type": "done", "conversation_id": "…", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
```

> 注意区分：`ChatResponse.usage` / `done.usage` 是**本轮**用量；`ConversationSummary.usage` 是**累计**用量。前端顶栏展示的是后者。

---

## 10. 前端展示

### 10.1 类型与数据来源（`src/api/chat.ts`）

`TokenUsage`（L26-31）与 `ConversationSummary.usage`（L39）对应后端契约。

**前端不解析 `done.usage`**：`streamChat` 的 `done` 分支只触发无参 `onDone?.()`（`chat.ts:222-225`）。用量改由**列表刷新**获得——每轮流式结束后 `App.tsx:528` 调 `loadConversations()`，拉回带累计 `usage` 的会话列表。`done.usage` 仍是 API 契约的一部分，供其他客户端 / 调试使用。

### 10.2 派生当前会话用量（`App.tsx:535-538`）

```tsx
const activeUsage = useMemo(
  () => conversations.find((item) => item.id === conversationId)?.usage ?? null,
  [conversations, conversationId]
);
```

不引入额外 state，直接从列表派生；列表刷新即自动更新。

### 10.3 顶栏胶囊（`App.tsx:655-663`）

```tsx
{activeUsage && (
  <span className="token-tag"
    title={`输入 ${activeUsage.input_tokens.toLocaleString()} · 输出 ${activeUsage.output_tokens.toLocaleString()} tokens`}>
    <TokenIcon size={13} />
    <span>本对话 {activeUsage.total_tokens.toLocaleString()} tokens</span>
  </span>
)}
```

- 主体显示**总量**，`toLocaleString()` 加千分位；悬停 `title` 显示**输入 / 输出**明细。
- `activeUsage` 为 `null` 时（未选会话 / 新建未发首条）整枚**隐藏**。
- 图标 `TokenIcon`（`Icons.tsx:127-135`）：三层椭圆堆叠的「数据 / 计数」意象。
- 样式 `.token-tag`（`App.css:370-381`）：`margin-left:auto` 顶到右侧、胶囊圆角、浅蓝底 `#eef1ff` / 蓝字 `#4d6bfe`、`font-variant-numeric: tabular-nums` 等宽数字防跳动。

---

## 11. 事务与容错

- **一轮一事务**：用户消息、助手消息、用量累加在同一 Session 内，轮末 `commit()` 成功后才返回 / 发 `done`（`chat_service.py:90`、`163`）。
- **失败回滚**：`Database.session()` 是 `contextmanager`，`except BaseException: session.rollback()`（`database.py:66-76`）；`get_db` 用 `scope="request"` 的 SessionDep，确保**流式响应迭代结束后**才清理（`db/dependencies.py:23-38`）。commit 前任何异常 → 消息与用量一起回滚，**不为失败轮次计数**，也不发 `done`。
- **上游缺 usage**：`_extract_usage(None)` 与 `stop.value or TokenUsage()` 双重兜底为 0，统计不阻塞回复。

---

## 12. 边界情况与已知局限

| 场景 | 行为 |
| --- | --- |
| 历史会话（0005 之前） | 三列 `server_default=0`，展示「本对话 0 tokens」 |
| 未选会话 / 新建未发首条 | `activeUsage=null`，胶囊隐藏 |
| 流式中断 / 上游无 usage | 本轮按 0 计；若整轮在 commit 前失败则回滚，不落库 |
| 开启思考（reasoning） | `reasoning_tokens` 已含在 `output_tokens`，总量自洽，无需单列 |
| prompt 缓存命中 | `cached_tokens` 是 `input_tokens` 子集，不单独持久化 |
| 并发多轮同一会话 | 原子 `col=col+delta`，无丢失更新 |

**已知局限（诚实标注）**：

- **搜索词改写调用的用量未计入**：开启联网搜索时，`_extract_search_queries` 会额外调一次廉价模型改写搜索词，其用量被丢弃（`chat_service.py:175` 的 `reply, _ = ai_client.chat(...)`）。当前会话累计**只覆盖主回答调用**，不含这次辅助调用。若需精确计费口径，可把该 `_` 也累加进会话。
- **只存三量**：`cached_tokens` / `reasoning_tokens` 未单列，无法在 UI 区分缓存命中或思考开销；如有需要可扩展 `TokenUsage` 与表列（再开一条迁移）。
- **累计而非明细**：无法回看「某一轮」用量；如需 per-turn 明细，要另建消息级用量存储。

---

## 13. 测试与验证

### 13.1 自动化测试（mock AIClient，零 AI 配额）

`conftest.py:90-106` 的 `ai_client` fixture 固定每轮用量 `10/5/15`：`chat.return_value=("测试回复", usage)`；`fake_stream` 产出两段增量后 `return usage`。

| 测试 | 位置 | 断言 |
| --- | --- | --- |
| 非流式响应含 usage | `test_api.py:47` | `usage == {10,5,15}` |
| 多轮累计翻倍 | `test_api.py:55` | 两轮后 `{20,10,30}` |
| 流式累计 | `test_api.py:67` | 列表 `usage == {10,5,15}` |
| SSE 事件序列含 done.usage | `test_api.py:114`（L130） | `done.usage == {10,5,15}` |
| PG 原子累加 | `test_postgres_integration.py:118` | 10/5/15 + 20/7/27 → `{30,12,42}` |
| 迁移 head + 零漂移 | `test_migrations.py:39` | head=0006、`compare_metadata==[]` |

### 13.2 本次复跑结果（真实实例）

- **SQLite**：token 专项 4 个 API 用例 + 迁移 9 个用例 → **13 passed**。
- **PostgreSQL `ai_chat_test`**（compose 容器，真实实例）：`test_token_usage_accumulates` + 迁移 head 用例 → **2 passed**。
- 测试库仅用 `ai_chat_test`（fixture 校验库名含 `test` 并清空业务表）；真实数据库 `ai_chat` 不被测试触碰。

### 13.3 前端验证（实现阶段）

- `tsc` 类型检查 + `vite build` 通过；lint 0 issue。
- 无头 Edge + CDP，注入 `window.fetch` 桩（**零 AI 调用、零 DB 写入**）实测：顶栏显示「本对话 4,260 tokens」、悬停「输入 2,700 · 输出 1,560 tokens」、未选 / 新建会话时胶囊隐藏。

---

## 附：关键代码索引

- `models/schemas.py`：`TokenUsage`(L48)、`ChatResponse.usage`(L58)、`ConversationSummary.usage`(L76)
- `services/strategy.py`：`chat` 返回元组(L85)、`chat_stream`(L87-109)、`_extract_usage`(L111-120)
- `services/ai_client.py`：`chat_stream` 的 `yield from` + `return`(L38-42)
- `services/chat_service.py`：非流式解包(L78)、累加(L87)、commit(L90)、`ChatResponse.usage`(L94)；流式 `next()`/`StopIteration`(L146-153)、累加(L160)、commit(L163)、`done.usage`(L164-170)；改写用量丢弃(L175)
- `services/memory_store.py`：`list_conversations.usage`(L39-43)、`add_token_usage`(L137-148)
- `db/orm_models.py`：`Conversation` 三列(L48-57)
- `migrations/versions/0005_conversation_token_usage.py`：`_COLUMNS`(L14)、`upgrade`(L17)、`downgrade`(L25)
- `db/database.py`：`session()` 回滚(L66-76)；`db/dependencies.py`：`get_db` / `SessionDep`(L23-38)
- `api/chat.py`：`/chat/chat`(L13)、`/chat/stream`(L20)；`api/conversations.py`：`GET ""`(L12)
- `ai-chat-frontend/src/api/chat.ts`：`TokenUsage`(L26)、`ConversationSummary.usage`(L39)、`done` 分支(L222)
- `ai-chat-frontend/src/App.tsx`：`loadConversations` 刷新(L528)、`activeUsage`(L535)、`token-tag`(L655)
- `ai-chat-frontend/src/App.css`：`.token-tag`(L370)；`src/Icons.tsx`：`TokenIcon`(L127)
- `docs/stream_output_example.md`：真实流式收尾事件序列与 usage 拆解