import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/main.css'

const app = createApp(App)

// 全局错误兜底：渲染/更新异常不至于白屏，至少给出可恢复的提示
app.config.errorHandler = (err, _instance, info) => {
  console.error('[ZhiCourt] unhandled error:', info, err)
}

// 部署更新后旧 HTML 引用已删除 chunk：自动整页刷新一次，避免白屏/死循环
let chunkReloaded = false
router.onError((error, to) => {
  const message = error instanceof Error ? error.message : String(error)
  if (/Failed to fetch dynamically imported module|Loading chunk \d+ failed|Importing a module script failed/.test(message)) {
    if (!chunkReloaded) {
      chunkReloaded = true
      sessionStorage.setItem('zhicourt:chunk-reload', '1')
      window.location.assign(router.resolve(to).fullPath)
    }
  }
})

app.use(createPinia())
app.use(router)
app.mount('#app')
