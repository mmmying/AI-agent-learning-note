import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { AUTH_EXPIRED_EVENT, apiFetch, authenticate, clearToken, fetchCurrentUser, getToken } from "../src/api/auth.ts";

const originalFetch = globalThis.fetch;
let values: Map<string, string>;
let expired: number;

beforeEach(() => {
  values = new Map();
  expired = 0;
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  } });
  const target = new EventTarget();
  target.addEventListener(AUTH_EXPIRED_EVENT, () => expired++);
  Object.defineProperty(globalThis, "window", { configurable: true, value: target });
});

afterEach(() => { globalThis.fetch = originalFetch; });

test("register stores token and authenticated requests attach it", async () => {
  const user = { id: "alice", username: "alice", created_at: "2026-10-22" };
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/api/auth/register");
    assert.deepEqual(JSON.parse(String(options?.body)), { username: "alice", password: "password-123" });
    return Response.json({ access_token: "alice-token", token_type: "bearer", user });
  };
  assert.deepEqual(await authenticate("register", "alice", "password-123"), user);
  assert.equal(getToken(), "alice-token");
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/api/auth/me");
    assert.equal(new Headers(options?.headers).get("Authorization"), "Bearer alice-token");
    return Response.json(user);
  };
  assert.deepEqual(await fetchCurrentUser(), user);
  clearToken();
  assert.equal(getToken(), null);
});

test("401 clears token and announces expired login", async () => {
  sessionStorage.setItem("ai-chat-token", "expired-token");
  globalThis.fetch = async () => Response.json({ detail: "登录过期" }, { status: 401 });
  await assert.rejects(apiFetch("/conversations"), /登录过期/);
  assert.equal(getToken(), null);
  assert.equal(expired, 1);
});

test("late 401 for previous account cannot clear new account token", async () => {
  sessionStorage.setItem("ai-chat-token", "alice-token");
  globalThis.fetch = async () => {
    sessionStorage.setItem("ai-chat-token", "bob-token");
    return Response.json({ detail: "expired" }, { status: 401 });
  };
  await assert.rejects(apiFetch("/conversations"), /expired/);
  assert.equal(getToken(), "bob-token");
  assert.equal(expired, 0);
});

test("AI provider errors preserve login", async () => {
  sessionStorage.setItem("ai-chat-token", "valid-token");
  globalThis.fetch = async () => Response.json({ detail: "AI 服务错误" }, { status: 502 });
  await assert.rejects(apiFetch("/chat/stream", { method: "POST" }), /AI 服务错误/);
  assert.equal(getToken(), "valid-token");
  assert.equal(expired, 0);
});

test("failed login does not store a token", async () => {
  globalThis.fetch = async () => Response.json({ detail: "用户名或密码错误" }, { status: 401 });
  await assert.rejects(authenticate("login", "alice", "bad-password"), /用户名或密码错误/);
  assert.equal(getToken(), null);
});
