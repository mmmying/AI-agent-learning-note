这是一个上手小项目

## 代码结构
```
mini-ai-agent/
├── app.py
├── client.py
├── config.py
└── logger.py
```

把它看成 **所有 AI SDK 的最小实现原型**

## 整个执行流程

```
用户输入消息
      ↓
app.py 接收输入
      ↓
创建 AIClient
      ↓
AIClient 构造请求数据
      ↓
AIClient 发送 HTTP 请求
      ↓
远程 API 返回 JSON
      ↓
AIClient 解析 JSON
      ↓
返回结果给 app.py
      ↓
app.py 输出结果
```

画成架构图：

```
┌─────────────┐
│   app.py    │
│ 程序入口层   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  AIClient   │
│ 业务通信层   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ requests    │
│ HTTP通信层  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Remote API  │
│ OpenAI等服务 │
└─────────────┘
```

以后所有 AI 项目都逃不掉这个结构

### 第一层 app.py——程序入口层

从架构角度看，它承担的是**协调程序执行流程**
它不负责发请求、解析json、处理http、记录日志，他只负责**告诉系统下一步该做什么**

### 第二层 AIClient——核心业务层

这是整个系统最核心的部分，职责是**负责和 AI 服务通信**

## 整个项目的设计思想
1.  分层

    app.py（程序入口层）-> client.py（业务通信层）-> requests（HTTP通信层）

2.  单一职责原则

    每个模块只做一件事

- app.py 负责程序流程
- AIClient 负责API通信
- create_payload() 负责构造数据
- logger 负责日志

3.  配置与代码分离

    配置单独放在config.py中，以后url改变、模型改变，不该业务代码

4.  异常处理

    try/except 网络失败，程序不会直接崩溃

5.  可观测性

    logger便于排查问题

