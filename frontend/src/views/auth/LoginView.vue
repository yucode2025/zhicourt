<script setup lang="ts">
// 登录页
import { computed, onMounted, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import { safeRedirect, saveOAuthIntent } from '@/utils/access'
import CourtClerk from '@/components/common/CourtClerk.vue'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const zhihuLoading = ref(false)
const errorMsg = ref('')
const required = computed(() => route.query.required === 'zhihu' ? 'zhihu' : route.query.required === 'authenticated' ? 'authenticated' : '')
const zhihuEnabled = computed(() => auth.capabilities?.zhihu_oauth.enabled === true)
const guestAllowed = computed(() => auth.capabilities?.usage_access_policy === 'guest')

onMounted(async () => {
  const err = typeof route.query.zhihu_error === 'string' ? route.query.zhihu_error : ''
  if (err) errorMsg.value = err
  await auth.fetchCapabilities()
  if (route.query.capability_error === '1' && auth.capabilitiesError) errorMsg.value = auth.capabilitiesError
})

function target(): string {
  return safeRedirect(route.query.redirect)
}

async function submit() {
  if (submitting.value) return
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  submitting.value = true
  errorMsg.value = ''
  try {
    const res = await auth.login(username.value.trim(), password.value)
    message.success(`欢迎回来，${res.user.nickname}`)
    // 保留上下文：登录前页面 redirect，否则回首页
    await auth.fetchCapabilities(true)
    router.push(required.value === 'zhihu'
      ? { path: '/profile', query: { tab: 'settings', redirect: target() } }
      : target())
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

async function loginWithZhihu() {
  if (zhihuLoading.value) return
  zhihuLoading.value = true
  errorMsg.value = ''
  try {
    saveOAuthIntent({ mode: 'login', redirect: target() })
    const res = await api.zhihuAuthorize()
    // 跳转知乎授权页；state 已由后端写入 HttpOnly Cookie 并落库
    window.location.href = res.authorize_url
  } catch (e) {
    sessionStorage.removeItem('zhicourt_oauth_intent')
    errorMsg.value = e instanceof ApiError ? e.message : '无法发起知乎登录，请稍后重试'
    zhihuLoading.value = false
  }
}
</script>

<template>
  <main class="page auth-page">
    <div class="auth-card card">
      <div class="auth-head">
        <CourtClerk :variant="errorMsg ? 'sleepy' : 'greet'" :size="88" />
        <h1>登录 ZhiCourt</h1>
        <p>登录后继续你的知识审理</p>
      </div>

      <form @submit.prevent="submit" novalidate>
        <div v-if="errorMsg" class="auth-error" role="alert">{{ errorMsg }}</div>
        <p v-if="required === 'zhihu'" class="auth-policy" role="status">当前业务功能仅向已绑定知乎的账号开放。请优先使用知乎登录；已有本站账号可登录后到个人设置绑定。</p>
        <p v-else-if="required === 'authenticated'" class="auth-policy" role="status">当前业务功能需要登录后使用。</p>

        <label class="field">
          <span>用户名</span>
          <input v-model="username" class="text-input" name="username" maxlength="30" autocomplete="username" placeholder="用户名" required />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" class="text-input" name="password" type="password" maxlength="72" autocomplete="current-password" placeholder="密码" required />
        </label>

        <template v-if="zhihuEnabled && required === 'zhihu'">
          <NButton type="primary" size="large" block :loading="zhihuLoading" :disabled="submitting || zhihuLoading" @click="loginWithZhihu">使用知乎登录</NButton>
          <div class="zhihu-divider" aria-hidden="true"><span>或使用本站账号</span></div>
        </template>

        <NButton attr-type="submit" :type="required === 'zhihu' ? 'default' : 'primary'" size="large" block :loading="submitting" :disabled="submitting">登 录</NButton>

        <template v-if="zhihuEnabled && required !== 'zhihu'">
          <div class="zhihu-divider" aria-hidden="true"><span>或</span></div>
          <NButton size="large" block :loading="zhihuLoading" :disabled="submitting || zhihuLoading" @click="loginWithZhihu">
            使用知乎登录
          </NButton>
          <p class="hint-text">授权确认在知乎完成，本站不会获得你的知乎密码。</p>
        </template>

        <p class="auth-foot">
          还没有账号？<RouterLink to="/register">立即注册</RouterLink>
          <template v-if="guestAllowed">
            <span class="sep">·</span>
            <RouterLink :to="target()">游客试用</RouterLink>
          </template>
        </p>
      </form>
    </div>
  </main>
</template>

<style scoped src="@/assets/auth.css" />
<style scoped>
.auth-policy { padding: 10px 12px; border-radius: 7px; background: var(--c-primary-bg); color: var(--c-ink-2); font-size: 13px; }
</style>
