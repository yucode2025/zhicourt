import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts'],
    css: true,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/@vue-flow/') || id.includes('\\@vue-flow\\')) return 'flow'
          if (id.includes('/echarts/') || id.includes('/zrender/')) return 'charts'
          if (id.includes('/naive-ui/') || id.includes('/vueuc/') || id.includes('/vdirs/')) return 'naive'
          if (id.includes('/vue/') || id.includes('/vue-router/') || id.includes('/pinia/')) return 'vendor'
          return undefined
        },
      },
    },
  },
})
