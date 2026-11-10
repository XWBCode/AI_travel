import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { onProxyError } from './vite.proxy'

/** 允许用环境变量临时指向别的后端（联调 / 排查代理问题时用） */
const API_TARGET = process.env.VITE_PROXY_TARGET || 'http://localhost:8080'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期由 Vite 代理到 Java 服务，避免跨域与硬编码地址
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        // SSE 必须关闭缓冲，否则流式输出会被攒成一坨
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
            }
          })
          // 必须赶在 Vite 自己的 error 处理之前注册（configure 先于它执行），
          // 否则会被它先回一个空 body 的 500。
          proxy.on('error', onProxyError)
        },
      },
    },
  },
  build: {
    // Element Plus 全量引入体积偏大，按需引入留到 Phase 1 收尾统一优化
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          'element-plus': ['element-plus'],
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
})
