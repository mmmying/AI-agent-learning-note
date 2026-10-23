export type User = { id: string; username: string; created_at: string };
type AuthResponse = { access_token: string; token_type: string; user: User };
const TOKEN_KEY = "ai-chat-token";
export const AUTH_EXPIRED_EVENT = "ai-chat-auth-expired";

// sessionStorage 随标签页关闭清除，并允许不同标签页登录不同账号。
export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

// 对 fetch 进行封装，自动添加 /api 前缀，自动添加 Authorization 头，并处理 401 错误
export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    if (response.status === 401 && token === getToken()) {
      clearToken();
      // 触发全局事件 AUTH_EXPIRED_EVENT，通知所有组件登录已过期
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `请求失败 (${response.status})`);
  }
  return response;
}

export async function authenticate(mode: "login" | "register", username: string, password: string): Promise<User> {
  const response = await fetch(`/api/auth/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "请检查用户名和密码格式");
  }
  const auth = data as AuthResponse;
  sessionStorage.setItem(TOKEN_KEY, auth.access_token);
  return auth.user;
}

// 获取当前用户信息
export async function fetchCurrentUser(): Promise<User> {
  return (await apiFetch("/auth/me")).json();
}
