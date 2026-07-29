import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // 将以 /api 开头的请求代理到后端 FastAPI 服务
      // 例如：前端请求 /api/chat -> 转发到 http://127.0.0.1:8000/chat
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 后端使用流式输出（SSE），需要关闭代理对响应的缓冲
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
