# 用户认证与多用户聊天

启动后端后执行 `npm install` 和 `npm run dev`。页面提供注册、登录和退出；注册后自动登录。用户名为 3–32 位字母、数字或下划线（不区分大小写），密码为 8–128 位字符。

所有聊天和会话请求自动携带 JWT。令牌保存在当前标签页的 `sessionStorage`，刷新后会验证登录状态；令牌失效时回到登录页。退出或登录过期时清空聊天界面并中止流式请求。每个用户只会看到自己的历史会话。

开发环境使用 Vite `/api` 代理访问 `http://127.0.0.1:8000`，生产环境需配置同等的反向代理并使用 HTTPS。后端需配置稳定的 `JWT_SECRET_KEY`，详见后端 README。

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
