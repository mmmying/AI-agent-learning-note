import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { AUTH_EXPIRED_EVENT, authenticate, clearToken, fetchCurrentUser, getToken, type User } from "./api/auth";

export default function AuthGate({ children }: { children: (user: User, logout: () => void) => ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(() => Boolean(getToken()));
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const expired = () => {
      setUser(null);
      setPassword("");
      setError("登录已过期，请重新登录");
    };
    // 监听全局事件 AUTH_EXPIRED_EVENT，当登录已过期时，调用 expired 函数
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    // 如果已登录，则获取当前用户信息
    if (getToken()) {
      fetchCurrentUser().then((value) => { if (active) setUser(value); })
        .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "登录状态检查失败"); })
        .finally(() => { if (active) setChecking(false); });
    }
    return () => { active = false; window.removeEventListener(AUTH_EXPIRED_EVENT, expired); };
  }, []);

  function logout() {
    clearToken();
    setUser(null);
    setPassword("");
    setError("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setUser(await authenticate(mode, username, password));
      setPassword("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "认证失败");
    } finally { setBusy(false); }
  }

  if (checking) return <div className="auth-page" role="status">正在检查登录状态...</div>;
  if (user) return children(user, logout);
  return <main className="auth-page">
    <form className="auth-card" onSubmit={submit}>
      <h1>AI Chat</h1>
      <p>{mode === "login" ? "登录，继续你的对话" : "创建账号，开启你的专属对话"}</p>
      <label htmlFor="username">用户名</label>
      <input id="username" value={username} onChange={(event) => setUsername(event.target.value)}
        autoComplete="username" required minLength={3} maxLength={32} pattern="[A-Za-z0-9_]{3,32}"
        title="3–32 位英文字母、数字或下划线，不区分大小写" disabled={busy} />
      <small>3–32 位英文字母、数字或下划线，不区分大小写</small>
      <label htmlFor="password">密码</label>
      <input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)}
        autoComplete={mode === "login" ? "current-password" : "new-password"}
        required minLength={8} maxLength={128} disabled={busy} />
      <small>8–128 位字符</small>
      {error && <div className="error" role="alert">{error}</div>}
      <button className="auth-submit" disabled={busy}>{busy ? "请稍候..." : mode === "login" ? "登录" : "注册并登录"}</button>
      <button type="button" className="auth-switch" disabled={busy} onClick={() => {
        setMode(mode === "login" ? "register" : "login"); setError(""); setPassword("");
      }}>{mode === "login" ? "没有账号？注册" : "已有账号？登录"}</button>
    </form>
  </main>;
}
