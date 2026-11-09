import { Modal } from "antd";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import remarkGfm from "remark-gfm";
import {
  type ConversationSummary,
  type Role,
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  streamChat,
} from "./api/chat";
import "./App.css";
import AuthGate from "./AuthGate";
import type { User } from "./api/auth";
import {
  ArrowUpIcon,
  BrandMark,
  EditIcon,
  LockIcon,
  LogoutIcon,
  PanelLeftIcon,
  SearchIcon,
  SparkIcon,
  StopIcon,
} from "./Icons";

type ChatMessage = {
  id: string;
  role: Role;
  content: string;
};

/** 后端 core/config.py 里的默认模型；后端没有模型列表接口，这里只做展示，不可切换 */
const MODEL_NAME = "deepseek-v4-flash";

const IS_APPLE = /Mac|iPhone|iPad/i.test(navigator.userAgent);
const NEW_CHAT_SHORTCUT = IS_APPLE ? "⌘ K" : "Ctrl K";

/**
 * Markdown 自定义渲染配置：
 * 带语言标记的代码块（```python 等）用 SyntaxHighlighter 高亮，
 * 行内代码和无语言标记的代码块保持默认渲染。
 */
const markdownComponents: Components = {
  code({ node: _node, className, children, ...props }) {
    const match = /language-(\w+)/.exec(className ?? "");

    if (!match) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    }

    return (
      <SyntaxHighlighter
        language={match[1]}
        style={oneDark}
        PreTag="div"
        /* 深色底与圆角由外层 .markdown-body pre 统一提供：
           oneDark 会把背景写进 code 的行内样式，CSS 盖不掉，只能从 codeTagProps 里去掉 */
        codeTagProps={{
          className: `language-${match[1]}`,
          style: { ...oneDark['code[class*="language-"]'], background: "transparent" },
        }}
        customStyle={{ margin: 0, padding: 0, background: "transparent", overflowX: "visible", fontSize: "0.88em" }}
      >
        {String(children).replace(/\n$/, "")}
      </SyntaxHighlighter>
    );
  },
};

function createMessage(role: Role, content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
  };
}

export default function App() {
  return <AuthGate>{(user, logout) => <ChatApp key={user.id} user={user} logout={logout} />}</AuthGate>;
}

function ChatApp({ user, logout }: { user: User; logout: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  /** 侧栏是否收起 */
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  /** 侧栏搜索框的关键字，按标题过滤本地会话列表 */
  const [query, setQuery] = useState("");
  /** 等待删除确认的会话，不为 null 时显示确认弹窗 */
  const [pendingDelete, setPendingDelete] = useState<ConversationSummary | null>(
    null
  );

  const abortControllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => { abortControllerRef.current?.abort(); }, []);
  /** 聊天消息的滚动容器 */
  const chatPanelRef = useRef<HTMLElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  /**
   * 是否“钉”在底部:为 true 时新消息到达会自动滚到底部;
   * 用户主动往上滚动查看历史时置为 false,避免流式输出打断阅读。
   */
  const isPinnedToBottomRef = useRef(true);

  /** 滚动到消息列表底部 */
  const scrollToBottom = useCallback(() => {
    const panel = chatPanelRef.current;
    if (panel) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, []);

  // 消息内容变化(新消息、流式增量)时,如果用户在底部附近就自动跟随滚动
  useEffect(() => {
    if (isPinnedToBottomRef.current) {
      scrollToBottom();
    }
  }, [messages, scrollToBottom]);

  // 输入框高度随内容增长，最高 160px 后内部滚动
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) {
      return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  /** 监听滚动,根据用户当前位置更新“是否钉在底部”的状态 */
  function handleChatScroll() {
    const panel = chatPanelRef.current;
    if (!panel) {
      return;
    }

    const distanceToBottom =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight;
    // 留出 40px 容差,轻微偏离底部仍视为“在底部”
    isPinnedToBottomRef.current = distanceToBottom < 40;
  }

  /** 拉取历史会话列表（按最近更新倒序） */
  const loadConversations = useCallback(async () => {
    try {
      setConversations(await fetchConversations());
    } catch {
      // 列表加载失败不阻塞聊天主流程，静默忽略
    }
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  /** 侧栏里实际渲染的会话：搜索关键字只影响展示，不重新请求后端 */
  const visibleConversations = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) {
      return conversations;
    }
    return conversations.filter((conversation) =>
      (conversation.title || "未命名会话").toLowerCase().includes(keyword)
    );
  }, [conversations, query]);

  /** 切换到指定的历史会话，并加载它的消息记录 */
  async function selectConversation(id: string) {
    if (id === conversationId) {
      return;
    }

    if (isStreaming) {
      stopStreaming();
    }

    setError(null);
    setConversationId(id);
    // 切换会话后总是定位到最新消息
    isPinnedToBottomRef.current = true;

    try {
      const data = await fetchConversationMessages(id);
      setMessages(data.map((item) => createMessage(item.role, item.content)));
    } catch (err) {
      setMessages([]);
      setError(err instanceof Error ? err.message : "加载会话消息失败");
    }
  }

  /** 用户在弹窗中点击“删除”后，真正执行删除；删的是当前会话则清空聊天窗口 */
  async function confirmDeleteConversation() {
    if (!pendingDelete) {
      return;
    }

    const conversation = pendingDelete;
    setPendingDelete(null);

    try {
      await deleteConversation(conversation.id);
      setConversations((prev) =>
        prev.filter((item) => item.id !== conversation.id)
      );

      if (conversation.id === conversationId) {
        startNewChat();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除会话失败");
    }
  }

  /** 停止正在进行的流式回复 */
  const stopStreaming = useCallback(() => {
    abortControllerRef.current?.abort();
    setIsStreaming(false);
  }, []);

  /** 开启一个新对话 */
  const startNewChat = useCallback(() => {
    stopStreaming();
    setMessages([]);
    setConversationId(null);
    setError(null);
    isPinnedToBottomRef.current = true;
    textareaRef.current?.focus();
  }, [stopStreaming]);

  // Ctrl/⌘ + K 新建对话，与侧栏“新对话”右侧的快捷键提示保持一致
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        startNewChat();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [startNewChat]);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const content = input.trim();

    if (!content || isStreaming) {
      return;
    }

    setError(null);
    setInput("");
    setIsStreaming(true);
    // 用户发送新消息时,总是回到底部并跟随流式输出
    isPinnedToBottomRef.current = true;

    const userMessage = createMessage("user", content);
    const assistantMessage = createMessage("assistant", "");

    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      await streamChat({
        conversationId,
        message: content,
        signal: abortController.signal,
        onStart: (id) => {
          setConversationId(id);
        },
        onDelta: (delta) => {
          setMessages((prev) =>
            prev.map((message) =>
              message.id === assistantMessage.id
                ? {
                    ...message,
                    content: message.content + delta,
                  }
                : message
            )
          );
        },
        onDone: () => {
          setIsStreaming(false);
        },
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("已停止生成");
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("未知错误");
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
      // 对话结束后刷新侧边栏，让新会话/最新排序及时出现
      loadConversations();
    }
  }

  const isEmpty = messages.length === 0;

  return (
    <div className={`app${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <BrandMark />
          <span className="sidebar-brand-name">AI Chat</span>
          <button
            type="button"
            className="icon-button"
            title="收起侧栏"
            aria-label="收起侧栏"
            onClick={() => setSidebarCollapsed(true)}
          >
            <PanelLeftIcon />
          </button>
        </div>

        <div className="sidebar-actions">
          <button type="button" className="nav-item" onClick={startNewChat}>
            <EditIcon />
            <span className="nav-item-label">新对话</span>
            <kbd className="nav-shortcut">{NEW_CHAT_SHORTCUT}</kbd>
          </button>

          <label className="nav-item search-item">
            <SearchIcon />
            <input
              value={query}
              placeholder="搜索对话"
              aria-label="搜索对话"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        </div>

        <nav className="conversation-list">
          <p className="list-label">最近的对话</p>

          {conversations.length === 0 ? (
            <p className="list-empty">你的灵感，从第一句开始。</p>
          ) : visibleConversations.length === 0 ? (
            <p className="list-empty">没有匹配的对话。</p>
          ) : (
            visibleConversations.map((conversation) => (
              <div
                key={conversation.id}
                className={`conversation-item ${
                  conversation.id === conversationId ? "active" : ""
                }`}
                title={conversation.title || "未命名会话"}
                onClick={() => selectConversation(conversation.id)}
              >
                <span className="conversation-title">
                  {conversation.title || "未命名会话"}
                </span>

                <button
                  className="conversation-delete"
                  title="删除会话"
                  aria-label="删除会话"
                  onClick={(event) => {
                    // 避免触发外层的“切换会话”点击
                    event.stopPropagation();
                    setPendingDelete(conversation);
                  }}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </nav>

        <div className="sidebar-footer">
          <span className="avatar" aria-hidden="true">
            {user.username.slice(0, 1).toUpperCase()}
          </span>
          <span className="account">
            <span className="account-name">{user.username}</span>
            <span className="account-state">已登录 · 对话保存在账号</span>
          </span>
          <button
            type="button"
            className="icon-button"
            title="退出登录"
            aria-label="退出登录"
            onClick={logout}
          >
            <LogoutIcon />
          </button>
        </div>
      </aside>

      <div className={`chat-window${isEmpty ? " is-empty" : ""}`}>
        <header className="topbar">
          {sidebarCollapsed && (
            <button
              type="button"
              className="icon-button"
              title="展开侧栏"
              aria-label="展开侧栏"
              onClick={() => setSidebarCollapsed(false)}
            >
              <PanelLeftIcon />
            </button>
          )}

          <span
            className="model-tag"
            title="后端当前配置的模型，暂不支持在页面切换"
          >
            <SparkIcon size={13} />
            <span>{MODEL_NAME}</span>
          </span>
        </header>

        <main className="chat-panel" ref={chatPanelRef} onScroll={handleChatScroll}>
          {isEmpty ? (
            <section className="hero">
              <p className="hero-eyebrow">
                <span className="hero-eyebrow-en">A little space for your ideas</span>
                <span className="hero-eyebrow-cn">给你的想法留一点空间</span>
              </p>
              <h2 className="hero-title">今天，想聊点什么？</h2>
              <p className="hero-subtitle">一个问题，一点灵感，或者一个新计划。</p>
            </section>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`message-row ${
                    message.role === "user" ? "user-row" : "assistant-row"
                  }`}
                >
                  <div className="message-bubble">
                    {message.role === "assistant" ? (
                      message.content ? (
                        <div className="markdown-body">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={markdownComponents}
                          >
                            {message.content}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <span className="thinking">正在思考…</span>
                      )
                    ) : (
                      <div className="message-content">{message.content}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>

        <div className="composer-area">
          {error && <div className="error" role="alert">{error}</div>}

          <form className="composer" onSubmit={sendMessage}>
            <textarea
              ref={textareaRef}
              value={input}
              disabled={isStreaming}
              placeholder="向 AI 提问"
              aria-label="输入消息"
              rows={1}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />

            <div className="composer-bar">
              <span className="composer-hint">
                <LockIcon size={13} />
                <span>对话保存在你的账号下</span>
              </span>

              {isStreaming ? (
                <button
                  type="button"
                  className="send-button stop"
                  title="停止生成"
                  aria-label="停止生成"
                  onClick={stopStreaming}
                >
                  <StopIcon />
                </button>
              ) : (
                <button
                  type="submit"
                  className="send-button"
                  title="发送"
                  aria-label="发送"
                  disabled={!input.trim()}
                >
                  <ArrowUpIcon />
                </button>
              )}
            </div>
          </form>

          <p className="disclaimer">AI 的回答可能不准确，请核实重要信息。</p>
        </div>
      </div>

      <Modal
        title="删除会话"
        open={pendingDelete !== null}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        onOk={confirmDeleteConversation}
        onCancel={() => setPendingDelete(null)}
      >
        确定删除会话「{pendingDelete?.title || "未命名会话"}」吗？删除后不可恢复。
      </Modal>
    </div>
  );
}
