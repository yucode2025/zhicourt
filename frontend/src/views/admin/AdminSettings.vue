<script setup lang="ts">
// 系统设置：Feature Flags + 运行参数 + Secret 状态（只显示 Configured/Missing）
import { computed, onMounted, ref } from 'vue'
import { NSelect, NSwitch, useDialog, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import type { SystemSettingItem } from '@/types'
import { fmtTime } from '@/utils/display'

const message = useMessage()
const dialog = useDialog()
const auth = useAuthStore()
const settings = ref<SystemSettingItem[]>([])
const secrets = ref<{ name: string; status: string }[]>([])
const loading = ref(false)
const actionError = ref('')
const pendingKeys = ref<Set<string>>(new Set())
const policyOptions = computed(() => [
  { label: '允许游客', value: 'guest' },
  { label: '仅登录用户', value: 'authenticated' },
  { label: '仅知乎用户', value: 'zhihu', disabled: !oauthConfigured.value },
])
const accessPolicy = computed(() => settings.value.find((item) => item.key === 'usage_access_policy'))
const oauthConfigured = computed(() => ['ZHIHU_OAUTH_APP_ID', 'ZHIHU_OAUTH_APP_KEY', 'ZHIHU_OAUTH_REDIRECT_URI']
  .every((name) => secrets.value.find((item) => item.name === name)?.status === 'configured'))

function setPending(key: string, value: boolean) {
  const next = new Set(pendingKeys.value)
  value ? next.add(key) : next.delete(key)
  pendingKeys.value = next
}

async function load() {
  if (loading.value) return
  loading.value = true
  actionError.value = ''
  try {
    const res = await api.adminSettings()
    settings.value = res.settings
    secrets.value = res.secrets
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '系统设置加载失败'
  } finally {
    loading.value = false
  }
}

function onFlagChange(item: SystemSettingItem, value: boolean) {
  const apply = async () => {
    if (pendingKeys.value.has(item.key)) return
    setPending(item.key, true)
    actionError.value = ''
    try {
      await api.adminSetSetting(item.key, value)
      item.value = value
      message.success(`已${value ? '开启' : '关闭'} ${item.key}`)
      await load()
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : '保存失败'
      message.error(actionError.value)
    } finally {
      setPending(item.key, false)
    }
  }
  if (!value && ['enable_mock_provider', 'allow_guest_cases'].includes(item.key)) {
    dialog.warning({
      title: '确认关闭',
      content: `关闭 ${item.key} 会影响游客使用或演示能力。确定继续？`,
      positiveText: '确定关闭',
      negativeText: '取消',
      onPositiveClick: apply,
    })
  } else {
    void apply()
  }
}

function savePolicy(value: 'guest' | 'authenticated' | 'zhihu') {
  const item = accessPolicy.value
  if (!item || value === item.value || pendingKeys.value.has(item.key)) return
  if (value === 'zhihu' && !oauthConfigured.value) {
    message.error('知乎 OAuth 配置不完整，不能启用“仅知乎用户”')
    return
  }
  const apply = async () => {
    setPending(item.key, true)
    try {
      await api.adminSetSetting(item.key, value)
      item.value = value
      message.success('访问策略已更新')
      await Promise.all([load(), auth.fetchCapabilities(true)])
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : '保存失败'
      message.error(actionError.value)
    } finally {
      setPending(item.key, false)
    }
  }
  dialog.warning({
    title: '确认变更业务访问策略',
    content: value === 'guest'
      ? '切换后未登录游客可使用业务路由并创建案件。确认开放？'
      : value === 'authenticated'
        ? '切换后游客将无法进入业务路由。公开分享、登录、个人中心和管理后台不受影响。'
        : '切换后仅知乎账号或已绑定知乎的本站账号可使用业务路由。普通账号仍可进入个人中心完成绑定。',
    positiveText: '确认并立即生效',
    negativeText: '取消',
    onPositiveClick: apply,
  })
}

async function saveNumber(item: SystemSettingItem, raw: string) {
  if (pendingKeys.value.has(item.key) || String(item.value) === raw) return
  const value = Number(raw)
  if (!Number.isFinite(value)) {
    actionError.value = '请输入有效数字'
    message.error(actionError.value)
    return
  }
  setPending(item.key, true)
  actionError.value = ''
  try {
    await api.adminSetSetting(item.key, value)
    item.value = value
    message.success('已保存')
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '保存失败'
    message.error(actionError.value)
  } finally {
    setPending(item.key, false)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>系统设置</h1>
    </div>

    <p v-if="actionError" class="admin-action-error" role="alert">{{ actionError }}</p>
    <div class="set-layout">
      <div>
        <h3 class="sec-h">业务访问策略</h3>
        <div v-if="accessPolicy" class="card flag-card policy-card">
          <div>
            <b class="flag-key">usage_access_policy</b>
            <p class="flag-desc">控制普通业务路由的体验门禁；公开分享、认证、个人中心和管理后台豁免。</p>
          </div>
          <NSelect
            :value="String(accessPolicy.value)"
            :options="policyOptions"
            :loading="pendingKeys.has('usage_access_policy')"
            :disabled="pendingKeys.has('usage_access_policy')"
            style="width: 180px"
            @update:value="savePolicy"
          />
          <p v-if="!oauthConfigured" class="policy-warning">知乎 OAuth 配置不完整，“仅知乎用户”不可启用。</p>
        </div>

        <h3 class="sec-h" style="margin-top: 22px">Feature Flags</h3>
        <div class="card flag-card" v-for="s in settings.filter((x) => x.is_flag)" :key="s.key">
          <div class="flag-row">
            <div>
              <b class="flag-key">{{ s.key }}</b>
              <p class="flag-desc">{{ s.description }}</p>
            </div>
            <NSwitch :value="Boolean(s.value)" size="small" :loading="pendingKeys.has(s.key)" :disabled="pendingKeys.has(s.key)" @update:value="(v: boolean) => onFlagChange(s, v)" />
          </div>
          <div v-if="s.updated_by" class="flag-meta">由 {{ s.updated_by }} 更新于 {{ fmtTime(s.updated_at ?? '') }}</div>
        </div>

        <h3 class="sec-h" style="margin-top: 22px">运行参数</h3>
        <div class="card flag-card" v-for="s in settings.filter((x) => !x.is_flag && x.key !== 'usage_access_policy')" :key="s.key">
          <div class="flag-row">
            <div>
              <b class="flag-key">{{ s.key }}</b>
              <p class="flag-desc">{{ s.description }}</p>
            </div>
            <input
              class="text-input num-input" :value="String(s.value)" :disabled="pendingKeys.has(s.key)"
              @keyup.enter="saveNumber(s, ($event.target as HTMLInputElement).value)"
              @change="saveNumber(s, ($event.target as HTMLInputElement).value)"
            />
          </div>
        </div>
      </div>

      <div>
        <h3 class="sec-h">Secret 状态</h3>
        <div class="card card-pad">
          <p class="hint-text" style="margin-top: 0">Secret 可在 Provider 页加密保存，也可由服务器环境变量提供；此处仅显示是否已配置，永不显示内容。</p>
          <div v-for="s in secrets" :key="s.name" class="secret-row">
            <span class="mono">{{ s.name }}</span>
            <span class="status-pill" :class="s.status === 'configured' ? 'HEALTHY' : 'BLOCKED'">
              {{ s.status === 'configured' ? '已配置' : '未配置' }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped src="@/assets/admin.css" />
<style scoped>
.admin-action-error { color: var(--c-danger); font-size: 13px; margin: 0 0 12px; }
.set-layout {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 20px;
  align-items: start;
}
.sec-h {
  font-size: 14.5px;
  margin-bottom: 10px;
}
.flag-card {
  padding: 13px 16px;
  margin-bottom: 10px;
}
.flag-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
}
.flag-key {
  font-family: var(--mono);
  font-size: 13px;
}
.flag-desc {
  font-size: 12.5px;
  color: var(--c-ink-3);
  margin: 3px 0 0;
}
.flag-meta {
  font-size: 11.5px;
  color: var(--c-ink-3);
  margin-top: 8px;
}
.policy-card { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.policy-card > div { flex: 1; min-width: 240px; }
.policy-warning { width: 100%; margin: 0; color: var(--c-danger); font-size: 12px; }
.num-input {
  width: 96px;
  text-align: right;
  font-family: var(--mono);
  padding: 6px 10px;
}
.secret-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;
  border-bottom: 1px dashed var(--c-line);
  font-size: 12.5px;
}
.secret-row:last-child {
  border-bottom: none;
}
@media (max-width: 900px) {
  .set-layout {
    grid-template-columns: 1fr;
  }
}
</style>
