import { Modal } from "antd";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import remarkGfm from "remark-gfm";
import {
  type ConversationSummary,
  type Role,
  type SearchResult,
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
  ChevronRightIcon,
  CloseIcon,
  EditIcon,
  GlobeIcon,
  LockIcon,
  LogoutIcon,
  PanelLeftIcon,
  SearchIcon,
  SparkIcon,
  StopIcon,
  TokenIcon,
} from "./Icons";

type SearchState = "searching" | "done" | "error";

type ChatMessage = {
  id: string;
  role: Role;
  content: string;
  /** 联网搜索状态，未开启搜索时为 undefined */
  searchState?: SearchState;
  sources?: SearchResult[];
  searchError?: string;
  /** 后端实际使用的聚焦搜索词 */
  searchQuery?: string;
};

const WEB_SEARCH_STORAGE_KEY = "ai-chat:web-search";

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

/** 从 URL 提取去 www 的域名，失败时退回原串 */
function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function originOf(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

/** 域名哈希到一组饱和色，让无图标时的字母头像也彼此可区分 */
const FAVICON_HUES = ["#4d6bfe", "#0ea5e9", "#16a34a", "#f59e0b", "#d946ef", "#ef4444", "#8b5cf6", "#14b8a6"];
function hueFor(domain: string): string {
  let hash = 0;
  for (const ch of domain) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return FAVICON_HUES[hash % FAVICON_HUES.length];
}

/** 站点图标：优先加载真实 favicon，失败时降级为域名首字母彩色头像 */
function Favicon({ url, size = 18 }: { url: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const domain = domainOf(url);
  const origin = originOf(url);
  const style = { width: size, height: size, fontSize: Math.round(size * 0.52) };
  if (failed || !origin) {
    return (
      <span className="favicon favicon-fallback" style={{ ...style, background: hueFor(domain) }} aria-hidden="true">
        {domain.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      className="favicon"
      style={style}
      src={`${origin}/favicon.ico`}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

/** 回答末尾的收起态来源入口：重叠站点图标 + 网页数，点击展开右侧抽屉 */
function SourcesPill({
  sources,
  query,
  onOpen,
}: {
  sources: SearchResult[];
  query?: string;
  onOpen: () => void;
}) {
  const seen = new Set<string>();
  const stack: SearchResult[] = [];
  for (const source of sources) {
    const domain = domainOf(source.url);
    if (!seen.has(domain)) {
      seen.add(domain);
      stack.push(source);
    }
    if (stack.length >= 4) break;
  }
  return (
    <div className="sources-pill-row">
      <button
        type="button"
        className="sources-pill"
        onClick={onOpen}
        aria-haspopup="dialog"
        title={query ? `搜索词：${query}` : "查看参考来源"}
      >
        <span className="favicon-stack" aria-hidden="true">
          {stack.map((source) => (
            <Favicon key={source.url} url={source.url} size={18} />
          ))}
        </span>
        <span className="sources-pill-count">{sources.length} 个网页</span>
        <ChevronRightIcon size={12} className="sources-pill-chevron" />
      </button>
    </div>
  );
}

/** 右侧「搜索结果」抽屉：逐条展示来源的站点、标题与摘要，点击在新标签页打开 */
function SourcesDrawer({
  sources,
  query,
  onClose,
}: {
  sources: SearchResult[];
  query?: string;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="sources-drawer-root" role="dialog" aria-modal="true" aria-label="搜索结果">
      <div className="sources-scrim" onClick={onClose} />
      <aside className="sources-drawer">
        <header className="sources-drawer-head">
          <div className="sources-drawer-titles">
            <h3>搜索结果</h3>
            {query && (
              <p className="sources-drawer-query" title={query}>
                搜索词：{query}
              </p>
            )}
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭搜索结果">
            <CloseIcon />
          </button>
        </header>
        <div className="sources-drawer-body">
          {sources.map((source, index) => (
            <a
              key={source.url}
              className="source-card"
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <div className="source-card-meta">
                <Favicon url={source.url} size={18} />
                <span className="source-card-domain">{domainOf(source.url)}</span>
                <span className="source-card-index">{index + 1}</span>
              </div>
              <h4 className="source-card-title">{source.title}</h4>
              {source.snippet && <p className="source-card-snippet">{source.snippet}</p>}
            </a>
          ))}
        </div>
      </aside>
    </div>
  );
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
  /** 右侧「搜索结果」抽屉展示的来源；为 null 时收起 */
  const [openSources, setOpenSources] = useState<{
    sources: SearchResult[];
    query?: string;
  } | null>(null);
  /** 联网搜索开关，偏好持久化到 localStorage，默认关闭 */
  const [webSearch, setWebSearch] = useState(() => {
    try {
      return localStorage.getItem(WEB_SEARCH_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(WEB_SEARCH_STORAGE_KEY, webSearch ? "1" : "0");
    } catch {
      // 隐私模式等场景下写入失败不影响使用
    }
  }, [webSearch]);

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
    setOpenSources(null);
    setConversationId(id);
    // 切换会话后总是定位到最新消息
    isPinnedToBottomRef.current = true;

    try {
      const data = await fetchConversationMessages(id);
      setMessages(
        data.map((item) => ({
          ...createMessage(item.role, item.content),
          sources: item.sources ?? [],
          searchQuery: item.search_query ?? undefined,
          searchState: item.sources && item.sources.length > 0 ? "done" : undefined,
        }))
      );
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
    setOpenSources(null);
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

    /** 只更新当前这条助手消息的搜索相关字段 */
    const patchAssistant = (patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessage.id ? { ...message, ...patch } : message
        )
      );
    };

    try {
      await streamChat({
        conversationId,
        message: content,
        webSearch,
        signal: abortController.signal,
        onStart: (id) => {
          setConversationId(id);
        },
        onSearchStart: (query) => {
          patchAssistant({ searchState: "searching", searchQuery: query });
        },
        onSearchResult: (sources, query) => {
          patchAssistant({ searchState: "done", sources, searchQuery: query });
        },
        onSearchError: (message) => {
          patchAssistant({ searchState: "error", searchError: message });
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

  /** 当前会话的累计 token 用量，取自侧栏列表（每轮对话结束后会刷新） */
  const activeUsage = useMemo(
    () => conversations.find((item) => item.id === conversationId)?.usage ?? null,
    [conversations, conversationId]
  );

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

          {activeUsage && (
            <span
              className="token-tag"
              title={`输入 ${activeUsage.input_tokens.toLocaleString()} · 输出 ${activeUsage.output_tokens.toLocaleString()} tokens`}
            >
              <TokenIcon size={13} />
              <span>本对话 {activeUsage.total_tokens.toLocaleString()} tokens</span>
            </span>
          )}
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
                      <>
                        {message.searchState === "searching" && (
                          <div className="search-status">
                            <span className="search-spinner" aria-hidden="true" />
                            <span>正在联网搜索…</span>
                            {message.searchQuery && (
                              <span className="search-query" title={message.searchQuery}>
                                {message.searchQuery}
                              </span>
                            )}
                          </div>
                        )}
                        {message.searchState === "error" && (
                          <div className="search-warning" role="status">
                            <GlobeIcon size={13} />
                            <span>{message.searchError}，已按普通模式回答</span>
                          </div>
                        )}
                        {message.content ? (
                          <div className="markdown-body">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={markdownComponents}
                            >
                              {message.content}
                            </ReactMarkdown>
                          </div>
                        ) : (
                          message.searchState !== "searching" && (
                            <span className="thinking">正在思考…</span>
                          )
                        )}
                        {message.searchState === "done" &&
                          message.sources &&
                          message.sources.length > 0 && (
                            <SourcesPill
                              sources={message.sources}
                              query={message.searchQuery}
                              onOpen={() =>
                                setOpenSources({
                                  sources: message.sources!,
                                  query: message.searchQuery,
                                })
                              }
                            />
                          )}
                      </>
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
              <div className="composer-tools">
                <button
                  type="button"
                  className={`search-toggle${webSearch ? " on" : ""}`}
                  aria-pressed={webSearch}
                  title={webSearch ? "联网搜索已开启，点击关闭" : "开启后回答会参考实时网页搜索结果"}
                  onClick={() => setWebSearch((value) => !value)}
                >
                  <GlobeIcon size={14} />
                  <span>联网搜索</span>
                  <span className="search-toggle-dot" aria-hidden="true" />
                </button>

                <span className="composer-hint">
                  <LockIcon size={13} />
                  <span>对话保存在你的账号下</span>
                </span>
              </div>

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

      {openSources && (
        <SourcesDrawer
          sources={openSources.sources}
          query={openSources.query}
          onClose={() => setOpenSources(null)}
        />
      )}
    </div>
  );
}
