# syntax=docker/dockerfile:1
# 前端生产镜像：Node 里编译出 dist，nginx 托管静态文件并把 /api 反代给后端容器。
#
#   docker compose -f ../docker-compose.yml build frontend
#   docker compose -f ../docker-compose.yml up -d frontend   # 然后 http://127.0.0.1:8080/
#
# 前端代码只用相对路径 /api 请求后端（见 src/api/），所以换后端地址不需要改代码或重新编译，
# 只改 nginx.conf 里的 proxy_pass；compose 网络里后端的服务名是 backend:8000。

# --- 构建层：只产出 dist，工具链不进最终镜像 ---
FROM node:24.22.1-alpine AS build

# 默认走官方 npm 源；需要换镜像源时用 --build-arg NPM_REGISTRY=... 覆盖。
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV npm_config_registry=${NPM_REGISTRY} \
    npm_config_fund=false \
    npm_config_audit=false

WORKDIR /app
# 先只拷 lockfile，改源码不会重装依赖。
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# --- 运行层 ---
FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1/"]
