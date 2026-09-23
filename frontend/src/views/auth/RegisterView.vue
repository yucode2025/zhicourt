<script setup lang="ts">
// 注册页
import { computed, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import CourtClerk from '@/components/common/CourtClerk.vue'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const username = ref('')
const nickname = ref('')
const password = ref('')
const password2 = ref('')
const submitting = ref(false)
const errorMsg = ref('')

const strength = computed(() => {
  const p = password.value
  if (!p) return 0
  let s = 0
  if (p.length >= 8) s++
  if (p.length >= 12) s++
  if (/[A-Z]/.test(p) && /[a-z]/.test(p)) s++
  if (/\d/.test(p)) s++
  if (/[^A-Za-z0-9]/.test(p)) s++
  return Math.min(s, 4)
})
const strengthLabel = ['过短', '弱', '一般', '良好', '强']

async function submit() {
  if (submitting.value) return
  errorMsg.value = ''
  if (username.value.trim().length < 3) {
    errorMsg.value = '用户名至少 3 个字符'
    return
  }
  if (password.value.length < 8) {
    errorMsg.value = '密码至少 8 位'
    return
  }
  if (password.value !== password2.value) {
    errorMsg.value = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    const res = await auth.register(username.value.trim(), password.value, nickname.value.trim() || undefined)
    message.success(res.migrated_cases > 0 ? `注册成功，已为你并入 ${res.migrated_cases} 个游客案件` : '注册成功')
    router.push('/')
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '注册失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="page auth-page">
    <div class="auth-card card">
      <div class="auth-head">
        <CourtClerk variant="greet" :size="88" />
        <h1>创建账号</h1>
        <p>注册后可保存案件、收藏判决书、追踪学习档案</p>
      </div>

      <form @submit.prevent="submit" novalidate>
        <div v-if="errorMsg" class="auth-error" role="alert">{{ errorMsg }}</div>

        <label class="field">
          <span>用户名</span>
          <input v-model="username" class="text-input" name="username" minlength="3" maxlength="30" autocomplete="username" placeholder="中文、字母、数字或下划线（≥3 字符）" required />
        </label>
        <label class="field">
          <span>昵称（可选）</span>
          <input v-model="nickname" class="text-input" name="nickname" maxlength="30" autocomplete="nickname" placeholder="展示名称" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" class="text-input" name="password" type="password" minlength="8" maxlength="72" autocomplete="new-password" placeholder="至少 8 位" required @input="errorMsg = ''" />
          <div v-if="password" class="pw-meter">
            <div class="pw-bar"><i :style="{ width: `${strength * 25}%` }" :data-level="strength" /></div>
            <em>{{ strengthLabel[strength] }}</em>
          </div>
        </label>
        <label class="field">
          <span>确认密码</span>
          <input v-model="password2" class="text-input" name="password-confirmation" type="password" minlength="8" maxlength="72" autocomplete="new-password" placeholder="再次输入密码" required />
        </label>

        <NButton attr-type="submit" type="primary" size="large" block :loading="submitting" :disabled="submitting">注 册</NButton>
        <p class="hint-text">当前浏览器里的游客案件会在注册后自动并入你的账号。</p>
        <p class="auth-foot">
          已有账号？<RouterLink to="/login">直接登录</RouterLink>
        </p>
      </form>
    </div>
  </main>
</template>

<style scoped src="@/assets/auth.css" />
<style scoped>
.pw-meter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.pw-bar {
  flex: 1;
  height: 4px;
  background: #edeff2;
  border-radius: 2px;
  overflow: hidden;
}
.pw-bar i {
  display: block;
  height: 100%;
  background: var(--c-warn);
  transition: width 0.2s;
}
.pw-bar i[data-level='4'] {
  background: var(--c-success);
}
.pw-bar i[data-level='3'] {
  background: #7ba05b;
}
.pw-meter em {
  font-style: normal;
  font-size: 11.5px;
  color: var(--c-ink-3);
}
</style>
