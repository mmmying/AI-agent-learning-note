# 用户认证与多用户聊天

启动后端后执行 `npm install` 和 `npm run dev`。页面提供注册、登录和退出；注册后自动登录。用户名为 3–32 位字母、数字或下划线（不区分大小写），密码为 8–128 位字符。

所有聊天和会话请求自动携带 JWT。令牌保存在当前标签页的 `sessionStorage`，刷新后会验证登录状态；令牌失效时回到登录页。退出或登录过期时清空聊天界面并中止流式请求。每个用户只会看到自己的历史会话。

开发环境使用 Vite `/api` 代理访问 `http://127.0.0.1:8000`。页面代码只请求相对路径 `/api/...`，所以换后端地址不用改代码，也不用重新编译。

镜像里由 nginx 干同一件事：`nginx.conf` 把 `/api/xxx` 反代成 `http://backend:8000/xxx`（剥掉 `/api` 前缀，与 Vite 的 `rewrite` 一致），其余路径回落 `index.html` 交给前端路由，`/api/` 还关掉了响应缓冲以支持 SSE 流式输出。用仓库根目录的 compose 起前后端：

```powershell
docker compose -f ../docker-compose.yml build frontend        # 首次要 npm ci
docker compose -f ../docker-compose.yml up -d                 # 打开 http://127.0.0.1:8080/
```

`frontend` 依赖 `backend` 健康后才启动，因为 nginx 在加载配置时就要解析 `backend` 这个服务名；只用 `docker compose up -d frontend` 而后端容器没起时，它会启动失败。宿主机 5173 通常已经被 `npm run dev` 占着，所以容器默认发布到 8080，可用 `FRONTEND_PORT` 改。HTTPS 需要在更外面一层终结，参见后端 README。后端需配置稳定的 `JWT_SECRET_KEY`。

验证：`npm run build`、`npm run lint`、`npm test`（认证测试需 Node.js 22.18+ 或 24+）。

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
