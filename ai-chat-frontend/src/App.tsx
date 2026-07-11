import { type FormEvent, useRef, useState } from "react";
import "./App.css";

type Role = "user" | "assistant";

type ChatMessage = {
  id: string;
  role: Role;
  content: string;
};

type SSEEventType = "start" | "delta" | "done";

type SSEEventData = {
  type: SSEEventType;
  conversation_id?: string;
  content?: string;
};

type SSEEvent = {
  event: string;
  data: SSEEventData;
};

function createMessage(role: Role, content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
  };
}

function parseSSEEvent(rawEvent: string): SSEEvent | null {
  const lines = rawEvent.split("\n");

  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  const rawData = dataLines.join("\n");

  try {
    return {
      event: eventName,
      data: JSON.parse(rawData),
    };
  } catch {
    return null;
  }
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const content = input.trim();

    if (!content || isStreaming) {
      return;
    }

    setError(null);
    setInput("");
    setIsStreaming(true);

    const userMessage = createMessage("user", content);
    const assistantMessage = createMessage("assistant", "");

    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          conversation_id: conversationId,
          message: content,
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }

      if (!response.body) {
        throw new Error("Response body is empty");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");

      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();

        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        const rawEvents = buffer.split("\n\n");

        buffer = rawEvents.pop() ?? "";

        for (const rawEvent of rawEvents) {
          const parsed = parseSSEEvent(rawEvent);

          if (!parsed) {
            continue;
          }

          // 后端没有使用 SSE 的 event: 字段，统一用 JSON 里的 type 字段区分事件
          switch (parsed.data.type) {
            case "start": {
              if (parsed.data.conversation_id) {
                setConversationId(parsed.data.conversation_id);
              }
              break;
            }

            case "delta": {
              const delta = parsed.data.content ?? "";

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
              break;
            }

            case "done": {
              setIsStreaming(false);
              break;
            }
          }
        }
      }
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
    }
  }

  function stopStreaming() {
    abortControllerRef.current?.abort();
    setIsStreaming(false);
  }

  function clearChat() {
    if (isStreaming) {
      stopStreaming();
    }

    setMessages([]);
    setConversationId(null);
    setError(null);
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>AI Chat</h1>
          <p>React + FastAPI + OpenAI Streaming</p>
        </div>

        <button className="secondary-button" onClick={clearChat}>
          清空对话
        </button>
      </header>

      <main className="chat-panel">
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
                    {message.content || "正在思考..."}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {conversationId && (
        <div className="conversation-id">
          conversation_id: <code>{conversationId}</code>
        </div>
      )}

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
  );
}