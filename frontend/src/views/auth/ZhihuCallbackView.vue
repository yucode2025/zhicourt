<script setup lang="ts">
// 知乎 OAuth 回调落地页：redirect_uri 指向本路由，前端把 code + state 交给后端换 Token
import { onMounted, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import { scalarQuery, takeOAuthIntent, type OAuthIntent } from '@/utils/access'
import CourtClerk from '@/components/common/CourtClerk.vue'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const auth = useAuthStore()

const errorMsg = ref('')
const working = ref(true)

async function finishLogin() {
  const authorizationCode = scalarQuery(route.query.authorization_code)
  const compatibleCode = scalarQuery(route.query.code)
  const state = scalarQuery(route.query.state)
  const intent: OAuthIntent = takeOAuthIntent() ?? { mode: 'login', redirect: '/' }
  // 授权码是一次性敏感材料，读取后立即从地址栏移除，避免进入截图与 Referrer。
  window.history.replaceState({}, document.title, route.path)
  if (!state || (!authorizationCode && !compatibleCode)) {
    errorMsg.value = '回调缺少授权参数，请重新发起知乎登录'
    working.value = false
    return
  }
  if (authorizationCode && compatibleCode && authorizationCode !== compatibleCode) {
    errorMsg.value = '回调包含冲突的授权码，已拒绝处理，请重新发起知乎登录'
    working.value = false
    return
  }
  const code = authorizationCode ?? compatibleCode
  if (!code) return
  try {
    const res = await api.zhihuCallback(code, state)
    auth.setUser(res.user)
    await auth.refreshSession()
    message.success(
      res.transferred
        ? '知乎账号已绑定到当前注册账号'
        : res.linked
          ? '知乎账号绑定成功'
          : res.created
            ? `知乎账号已关联，欢迎 ${res.user.nickname}`
            : `欢迎回来，${res.user.nickname}`,
    )
    router.replace(intent.redirect)
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '知乎登录失败，请稍后重试'
    working.value = false
  }
}

function backToLogin() {
  router.replace('/login')
}

onMounted(finishLogin)
</script>

<template>
  <main class="page auth-page">
    <div class="auth-card card">
      <div class="auth-head">
        <CourtClerk :variant="errorMsg ? 'sleepy' : 'greet'" :size="88" />
        <h1>知乎登录</h1>
        <p v-if="working">正在确认你的知乎授权…</p>
        <p v-else-if="errorMsg" class="auth-error" role="alert">{{ errorMsg }}</p>
      </div>
      <NButton v-if="!working" size="large" block @click="backToLogin">返回登录</NButton>
    </div>
  </main>
</template>

<style scoped src="@/assets/auth.css" />
