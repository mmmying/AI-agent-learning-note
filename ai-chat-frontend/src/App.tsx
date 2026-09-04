import { Modal } from "antd";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
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

type ChatMessage = {
  id: string;
  role: Role;
  content: string;
};

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
        customStyle={{ margin: "8px 0", borderRadius: 10, fontSize: "0.88em" }}
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  /** 等待删除确认的会话，不为 null 时显示确认弹窗 */
  const [pendingDelete, setPendingDelete] = useState<ConversationSummary | null>(
    null
  );

  const abortControllerRef = useRef<AbortController | null>(null);
  /** 聊天消息的滚动容器 */
  const chatPanelRef = useRef<HTMLElement | null>(null);
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

  /** 开启一个新对话 */
  function startNewChat() {
    if (isStreaming) {
      stopStreaming();
    }

    setMessages([]);
    setConversationId(null);
    setError(null);
    isPinnedToBottomRef.current = true;
  }

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

  function stopStreaming() {
    abortControllerRef.current?.abort();
    setIsStreaming(false);
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>AI Chat</h1>
          <button className="new-chat-button" onClick={startNewChat}>
            + 新对话
          </button>
        </div>

        <nav className="conversation-list">
          {conversations.length === 0 ? (
            <p className="conversation-empty">暂无历史会话</p>
          ) : (
            conversations.map((conversation) => (
              <div
                key={conversation.id}
                className={`conversation-item ${
                  conversation.id === conversationId ? "active" : ""
                }`}
                onClick={() => selectConversation(conversation.id)}
              >
                <div className="conversation-info">
                  <span className="conversation-title">
                    {conversation.title || "未命名会话"}
                  </span>
                  <span className="conversation-time">
                    {conversation.updated_at}
                  </span>
                </div>

                <button
                  className="conversation-delete"
                  title="删除会话"
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
      </aside>

      <div className="chat-window">
        <main className="chat-panel" ref={chatPanelRef} onScroll={handleChatScroll}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>开始一段对话</h2>
              <p>输入问题后，后端会通过流式接口逐段返回 AI 回复。</p>
            </div>
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
                    <div className="message-role">
                      {message.role === "user" ? "你" : "AI"}
                    </div>
                    <div className="message-content">
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
                          "正在思考..."
                        )
                      ) : (
                        message.content
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>

        {error && <div className="error">{error}</div>}

        <form className="input-panel" onSubmit={sendMessage}>
          <textarea
            value={input}
            disabled={isStreaming}
            placeholder="输入你的问题..."
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />

          <div className="actions">
            {isStreaming ? (
              <button type="button" onClick={stopStreaming}>
                停止
              </button>
            ) : (
              <button type="submit" disabled={!input.trim()}>
                发送
              </button>
            )}
          </div>
        </form>
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
