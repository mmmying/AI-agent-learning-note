/**
 * 与后端 FastAPI 交互的接口封装。
 * 所有请求都走 /api 前缀，由 Vite 代理转发到后端服务。
 */

export type Role = "user" | "assistant";

/** 后端返回的消息结构 */
export type ApiMessage = {
  role: Role;
  content: string;
};

/** 历史会话摘要，对应后端 /api/conversations 返回的每一项 */
export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

type SSEEventType = "start" | "delta" | "done";

type SSEEventData = {
  type: SSEEventType;
  conversation_id?: string;
  content?: string;
};

/** 流式聊天的参数：请求内容 + 各类事件的回调 */
export type StreamChatParams = {
  conversationId: string | null;
  message: string;
  signal?: AbortSignal;
  /** 收到会话 ID 时触发（新会话时后端会先返回 ID） */
  onStart?: (conversationId: string) => void;
  /** 收到一段增量回复时触发 */
  onDelta?: (delta: string) => void;
  /** 回复全部结束时触发 */
  onDone?: () => void;
};

/**
 * 解析一条原始 SSE 事件文本，返回其中的 JSON 数据
 * @param rawEvent 两个空行之间的原始事件文本
 */
function parseSSEEventData(rawEvent: string): SSEEventData | null {
  const dataLines: string[] = [];

  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  try {
    return JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
}

/**
 * 获取历史会话列表（按最近更新倒序）
 */
export async function fetchConversations(): Promise<ConversationSummary[]> {
  const response = await fetch("/api/conversations");

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json();
}

/**
 * 获取指定会话的消息列表（按时间顺序）
 * @param conversationId 会话 ID
 */
export async function fetchConversationMessages(
  conversationId: string
): Promise<ApiMessage[]> {
  const response = await fetch(`/api/conversations/${conversationId}/messages`);

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json();
}

/**
 * 删除指定会话（该会话下的消息会一并删除）
 * @param conversationId 会话 ID
 */
export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
}

/**
 * 发起流式聊天请求，逐段解析 SSE 响应并通过回调通知调用方
 */
export async function streamChat({
  conversationId,
  message,
  signal,
  onStart,
  onDelta,
  onDone,
}: StreamChatParams): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
    }),
    signal,
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

    // SSE 事件之间以空行（\n\n）分隔，最后一段可能不完整，留到下一轮拼接
    const rawEvents = buffer.split("\n\n");
    buffer = rawEvents.pop() ?? "";

    for (const rawEvent of rawEvents) {
      const data = parseSSEEventData(rawEvent);

      if (!data) {
        continue;
      }

      // 后端没有使用 SSE 的 event: 字段，统一用 JSON 里的 type 字段区分事件
      switch (data.type) {
        case "start": {
          if (data.conversation_id) {
            onStart?.(data.conversation_id);
          }
          break;
        }

        case "delta": {
          onDelta?.(data.content ?? "");
          break;
        }

        case "done": {
          onDone?.();
          break;
        }
      }
    }
  }
}
