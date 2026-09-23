<script setup lang="ts">
// Provider 状态 + 接入配置（官方规范 + LLM）+ 数据完整性检查
import { computed, onMounted, reactive, ref } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { IntegrityReport, ProviderStatus } from '@/types'
import { fmtTime } from '@/utils/display'

const message = useMessage()

const items = ref<ProviderStatus[]>([])
const integrity = ref<IntegrityReport | null>(null)
const checking = ref(false)
const loading = ref(false)
const error = ref('')
const actionError = ref('')

// ---- 接入配置 ----
const cfgItems = ref<
  { key: string; description: string; is_secret: boolean; value: string; from_db: boolean; set: boolean; group: string }[]
>([])
const cfgMode = ref<{ zhihu: string; web: string; llm: string; oauth: string }>({
  zhihu: 'MOCK', web: 'MOCK', llm: 'FALLBACK', oauth: 'DISABLED',
})
const cfgForm = reactive<Record<string, string>>({})
const secretClears = reactive<Record<string, boolean>>({})
const savingCfg = ref(false)
const testingCfg = ref(false)
const testingLlm = ref(false)
const testingLlmSlot = ref('')
const testResult = ref<{ ok: boolean; message?: string; error?: string; hint?: string; latency_ms?: number } | null>(null)
const llmTestResult = ref<{ ok: boolean; message?: string; error?: string; latency_ms?: number } | null>(null)

const cfgContentItems = computed(() => cfgItems.value.filter((i) => i.group === 'content'))
const cfgOauthItems = computed(() => cfgItems.value.filter((i) => i.group === 'oauth'))
const cfgLlmItems = computed(() => cfgItems.value.filter((i) => i.group === 'llm'))
const LLM_SLOTS = [
  { prefix: 'llm', label: '主接口', order: 1 },
  { prefix: 'llm_fallback_1', label: '备用接口 1', order: 2 },
  { prefix: 'llm_fallback_2', label: '备用接口 2', order: 3 },
] as const
const cfgLlmSlots = computed(() => LLM_SLOTS.map((slot) => ({
  ...slot,
  items: cfgLlmItems.value.filter((item) => [
    `${slot.prefix}_base_url`, `${slot.prefix}_api_key`, `${slot.prefix}_model`,
  ].includes(item.key)),
})))
type ConfigGroup = 'content' | 'oauth' | 'llm'

const MODE_DESC: Record<string, string> = {
  REAL: '真实 API',
  MOCK: '演示 Provider',
  FALLBACK: '降级模式',
  DISABLED: '已由管理员关闭',
  NOT_USED: '暂未启用',
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    items.value = (await api.adminProviders()).items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  }
  try {
    const cfg = await api.adminProviderConfig()
    cfgItems.value = cfg.items
    cfgMode.value = cfg.mode
    for (const i of cfg.items) {
      cfgForm[i.key] = i.value
      secretClears[i.key] = false
    }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : 'Provider 配置加载失败'
  } finally {
    loading.value = false
  }
}

function clearSecret(key: string) {
  cfgForm[key] = ''
  secretClears[key] = true
}

function markSecretEdited(key: string, isSecret: boolean) {
  if (isSecret) secretClears[key] = false
}

async function saveConfig(group: ConfigGroup) {
  if (savingCfg.value) return
  savingCfg.value = true
  actionError.value = ''
  testResult.value = null
  llmTestResult.value = null
  try {
    const fields: Record<string, string> = {}
    for (const i of cfgItems.value.filter((item) => item.group === group)) {
      const v = (cfgForm[i.key] ?? '').trim()
      // 掩码原样 = 未修改；secret 只有显式点击清空才提交空串。
      if (i.is_secret && secretClears[i.key]) fields[i.key] = ''
      else if (v && !(i.is_secret && v.startsWith('••••')) && v !== i.value) fields[i.key] = v
      else if (!v && i.from_db && !i.is_secret) fields[i.key] = ''
    }
    const res = await api.adminSaveProviderConfig(fields)
    const n = Object.keys(res.changes).length
    if (group === 'llm') {
      message.success(`LLM 配置已热更新，模式：${res.mode.llm === 'REAL' ? '真实 LLM' : '启发式降级'}`)
    } else if (group === 'oauth') {
      message.success(`知乎 OAuth 配置已热更新，状态：${res.mode.oauth === 'REAL' ? '已启用' : '未启用'}`)
    } else {
      message.success(n ? `已保存 ${n} 项，模式：知乎 ${res.mode.zhihu} / 全网 ${res.mode.web}` : '没有变更')
    }
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '保存失败'
    message.error(actionError.value)
  } finally {
    savingCfg.value = false
  }
}

async function testLlm(slot: (typeof LLM_SLOTS)[number]) {
  if (testingLlm.value) return
  testingLlm.value = true
  testingLlmSlot.value = slot.prefix
  actionError.value = ''
  llmTestResult.value = null
  try {
    const fields: Record<string, string> = {}
    const target = cfgLlmSlots.value.find((item) => item.prefix === slot.prefix)
    for (const i of target?.items ?? []) {
      const v = (cfgForm[i.key] ?? '').trim()
      if (v) fields[i.key] = v
    }
    fields.llm_slot = slot.prefix
    llmTestResult.value = await api.adminTestLlmConfig(fields)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : 'LLM 测试失败'
    llmTestResult.value = { ok: false, error: actionError.value }
  } finally {
    testingLlm.value = false
    testingLlmSlot.value = ''
  }
}

async function testConfig() {
  if (testingCfg.value) return
  testingCfg.value = true
  actionError.value = ''
  testResult.value = null
  try {
    const fields: Record<string, string> = {}
    for (const i of cfgItems.value) {
      const v = (cfgForm[i.key] ?? '').trim()
      if (v) fields[i.key] = v
    }
    testResult.value = await api.adminTestProviderConfig(fields)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '连通性测试失败'
    testResult.value = { ok: false, error: actionError.value }
  } finally {
    testingCfg.value = false
  }
}

async function runIntegrity() {
  if (checking.value) return
  checking.value = true
  actionError.value = ''
  try {
    integrity.value = await api.adminIntegrity()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '完整性检查失败'
  } finally {
    checking.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>Provider 状态</h1>
      <NButton size="small" :loading="loading" :disabled="loading" @click="load">刷新</NButton>
    </div>

    <div v-if="error" class="error-state card"><p>{{ error }}</p></div>
    <p v-if="actionError" class="admin-action-error" role="alert">{{ actionError }}</p>

    <!-- 接入配置：填 Key 即生效 -->
    <div class="card card-pad cfg-card">
      <div class="cfg-head">
        <div>
          <h3 class="cfg-title">知乎官方 API 接入（按官方 OpenAPI 规范）</h3>
          <p class="hint-text" style="margin: 4px 0 0">
            填写 <b>Access Secret</b>（developer.zhihu.com 个人中心获取），保存后<b>立即生效</b>，无需重启。
            Base URL 与各路径留空即使用官方默认（https://developer.zhihu.com）；
            全网搜索与知乎搜索共用同一 Access Secret 时，两处填同一个即可。
            Key 仅显示末 4 位；优先级：此处配置 &gt; 服务器 .env &gt; 演示模式。
          </p>
        </div>
        <div class="cfg-mode">
          <span class="mode-pill" :class="cfgMode.zhihu">知乎 {{ cfgMode.zhihu }}</span>
          <span class="mode-pill" :class="cfgMode.web">全网 {{ cfgMode.web }}</span>
        </div>
      </div>

      <div class="cfg-grid">
        <label v-for="i in cfgContentItems" :key="i.key" class="cfg-field" :data-testid="`config-${i.key}`">
          <span class="cfg-key">{{ i.key }} <em v-if="i.from_db" class="cfg-db">已自定义</em></span>
          <div class="cfg-input-row">
            <NInput
              v-model:value="cfgForm[i.key]"
              size="small"
              :placeholder="i.set ? '（留空不会修改 Secret）' : '未配置'"
              :type="i.is_secret ? 'password' : 'text'"
              :show-password-on="i.is_secret ? 'click' : undefined"
              @update:value="markSecretEdited(i.key, i.is_secret)"
            />
            <NButton
              v-if="i.is_secret && i.from_db"
              size="small"
              secondary
              type="error"
              :data-testid="`clear-secret-${i.key}`"
              @click="clearSecret(i.key)"
            >清空</NButton>
          </div>
          <em class="cfg-desc">{{ secretClears[i.key] ? '保存后显式清空数据库 Secret，并阻止回退到服务器环境变量。' : i.description }}</em>
        </label>
      </div>

      <div class="cfg-actions">
        <NButton size="small" :loading="loading" :disabled="loading" @click="load">重置</NButton>
        <NButton size="small" type="warning" :loading="testingCfg" :disabled="testingCfg || savingCfg" @click="testConfig">
          连通性测试（消耗 1 次官方调用）
        </NButton>
        <NButton size="small" type="primary" :loading="savingCfg" :disabled="savingCfg || testingCfg || testingLlm" @click="saveConfig('content')">保存配置</NButton>
      </div>

      <div v-if="testResult" class="test-result" :class="testResult.ok ? 'ok' : 'bad'">
        <template v-if="testResult.ok">
          ✅ {{ testResult.message }}（{{ testResult.latency_ms }}ms）
          <div v-if="testResult.hint" class="test-hint">{{ testResult.hint }}</div>
        </template>
        <template v-else>
          ❌ {{ testResult.error }}<template v-if="testResult.latency_ms !== undefined">（{{ testResult.latency_ms }}ms）</template>
          <div v-if="testResult.hint" class="test-hint">{{ testResult.hint }}</div>
        </template>
      </div>
    </div>

    <!-- 知乎 OAuth 登录配置 -->
    <div class="card card-pad cfg-card" style="margin-top: 16px; border-left-color: #1677ff">
      <div class="cfg-head">
        <div>
          <h3 class="cfg-title">知乎 OAuth 登录</h3>
          <p class="hint-text" style="margin: 4px 0 0">
            这里填写黑客松赛事页面分配的 <b>App ID</b> 与 <b>App Key</b>，它们不同于内容 API 的 Access Secret。
            回调地址须先在赛事页面登记，并使用本站 HTTPS 地址，路径固定为 <b>/login/zhihu</b>。
            App Key 加密保存；保存后立即生效，无需重启服务。
          </p>
        </div>
        <span class="mode-pill" :class="cfgMode.oauth === 'REAL' ? 'REAL' : 'DISABLED'">
          {{ cfgMode.oauth === 'REAL' ? '已启用' : '未配置' }}
        </span>
      </div>

      <div class="cfg-grid">
        <label v-for="i in cfgOauthItems" :key="i.key" class="cfg-field" :data-testid="`config-${i.key}`">
          <span class="cfg-key">{{ i.key }} <em v-if="i.from_db" class="cfg-db">已自定义</em></span>
          <div class="cfg-input-row">
            <NInput
              v-model:value="cfgForm[i.key]"
              size="small"
              :placeholder="i.set ? '（留空不会修改 Secret）' : '未配置'"
              :type="i.is_secret ? 'password' : 'text'"
              :show-password-on="i.is_secret ? 'click' : undefined"
              @update:value="markSecretEdited(i.key, i.is_secret)"
            />
            <NButton
              v-if="i.is_secret && i.from_db"
              size="small"
              secondary
              type="error"
              :data-testid="`clear-secret-${i.key}`"
              @click="clearSecret(i.key)"
            >清空</NButton>
          </div>
          <em class="cfg-desc">{{ secretClears[i.key] ? '保存后显式清空数据库 Secret，并阻止回退到服务器环境变量。' : i.description }}</em>
        </label>
      </div>

      <div class="cfg-actions">
        <NButton size="small" :loading="loading" :disabled="loading" @click="load">重置</NButton>
        <NButton size="small" type="primary" :loading="savingCfg" :disabled="savingCfg || testingCfg || testingLlm" @click="saveConfig('oauth')">保存 OAuth 配置</NButton>
      </div>
    </div>

    <!-- LLM 配置 -->
    <div class="card card-pad cfg-card" style="margin-top: 16px; border-left-color: #6a56b8">
      <div class="cfg-head">
        <div>
          <h3 class="cfg-title">LLM 配置（OpenAI 兼容接口）</h3>
          <p class="hint-text" style="margin: 4px 0 0">
            最多配置 3 个上游，按主接口 → 备用 1 → 备用 2 自动故障转移；只有全部失败才回退启发式模式。
            填写后保存<b>立即热生效</b>。
            Base URL 通常以 <b>/v1</b> 结尾（如 https://api.deepseek.com/v1）。API Key <b>Fernet 加密保存</b>，仅显示末 4 位。
          </p>
        </div>
        <span class="mode-pill" :class="cfgMode.llm === 'REAL' ? 'REAL' : 'FALLBACK'">
          {{ cfgMode.llm === 'REAL' ? '真实 LLM' : '启发式降级' }}
        </span>
      </div>

      <div class="llm-slots">
        <section v-for="slot in cfgLlmSlots" :key="slot.prefix" class="llm-slot">
          <div class="llm-slot-head">
            <div>
              <b>{{ slot.label }}</b>
              <span class="failover-order">优先级 {{ slot.order }}</span>
            </div>
            <NButton
              size="tiny"
              secondary
              :loading="testingLlm && testingLlmSlot === slot.prefix"
              :disabled="testingLlm || savingCfg"
              @click="testLlm(slot)"
            >测试此接口</NButton>
          </div>
          <div class="cfg-grid">
            <label v-for="i in slot.items" :key="i.key" class="cfg-field" :data-testid="`config-${i.key}`">
              <span class="cfg-key">{{ i.key }} <em v-if="i.from_db" class="cfg-db">已自定义</em></span>
              <div class="cfg-input-row">
                <NInput
                  v-model:value="cfgForm[i.key]"
                  size="small"
                  :placeholder="i.set ? '（留空不会修改 Secret）' : '未配置'"
                  :type="i.is_secret ? 'password' : 'text'"
                  :show-password-on="i.is_secret ? 'click' : undefined"
                  @update:value="markSecretEdited(i.key, i.is_secret)"
                />
                <NButton
                  v-if="i.is_secret && i.from_db"
                  size="small"
                  secondary
                  type="error"
                  :data-testid="`clear-secret-${i.key}`"
                  @click="clearSecret(i.key)"
                >清空</NButton>
              </div>
              <em class="cfg-desc">{{ secretClears[i.key] ? '保存后显式清空数据库 Secret，并阻止回退到服务器环境变量。' : i.description }}</em>
            </label>
          </div>
        </section>
      </div>

      <div class="cfg-actions">
        <NButton size="small" type="primary" :loading="savingCfg" :disabled="savingCfg || testingCfg || testingLlm" @click="saveConfig('llm')">保存 LLM 配置</NButton>
      </div>

      <div v-if="llmTestResult" class="test-result" :class="llmTestResult.ok ? 'ok' : 'bad'">
        <template v-if="llmTestResult.ok">✅ {{ llmTestResult.message }}（{{ llmTestResult.latency_ms }}ms）</template>
        <template v-else>❌ {{ llmTestResult.error }}<template v-if="llmTestResult.latency_ms !== undefined">（{{ llmTestResult.latency_ms }}ms）</template></template>
      </div>
    </div>

    <!-- 状态表 -->
    <div v-if="!error" class="card provider-status-card" style="overflow-x: auto; margin-top: 16px">
      <div class="provider-status-head">
        <div>
          <h3>接口运行状态</h3>
          <p>这里只展示接入模式与健康状态，调用量和官方额度统一在 API 用量页查看。</p>
        </div>
        <RouterLink to="/admin/api-usage">查看 API 用量 →</RouterLink>
      </div>
      <table class="tbl">
        <thead>
          <tr><th>Provider</th><th>Mode</th><th>健康状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in items" :key="p.key">
            <td><b>{{ p.name }}</b></td>
            <td>
              <span class="mode-pill" :class="p.mode">{{ p.mode }}</span>
              <div class="mode-desc">{{ MODE_DESC[p.mode] }}</div>
            </td>
            <td><span class="status-pill" :class="p.status">{{ p.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 数据完整性 -->
    <div class="pg-head" style="margin-top: 26px">
      <h1 style="font-size: 17px">数据完整性检查</h1>
      <NButton size="small" type="primary" :loading="checking" :disabled="checking" @click="runIntegrity">运行检查</NButton>
    </div>
    <div v-if="integrity" class="card card-pad">
      <div class="integ-head">
        <span class="status-pill" :class="integrity.status">{{ integrity.status }}</span>
        <span class="dim" style="font-size: 12.5px">检查于 {{ fmtTime(integrity.checked_at) }} · {{ integrity.issue_count }} 项发现</span>
      </div>
      <div v-if="!integrity.issues.length" class="hint-text" style="margin-top: 10px">未发现问题：证据均有来源，引用完整，Mock 标记规范。</div>
      <div v-for="(i, idx) in integrity.issues" :key="idx" class="integ-issue" :class="i.level.toLowerCase()">
        <span class="integ-level">{{ i.level }}</span>
        <span class="integ-kind">{{ i.kind }}</span>
        <span class="integ-detail">{{ i.detail }}</span>
      </div>
    </div>
    <div v-else class="hint-text" style="margin-top: 10px">点击「运行检查」验证 Evidence→Source 可追溯性、引用完整性与 Mock 标记。</div>
  </div>
</template>

<style scoped src="@/assets/admin.css" />
<style scoped>
.admin-action-error { color: var(--c-danger); font-size: 13px; margin: 0 0 12px; }
.cfg-card {
  border-left: 4px solid var(--c-primary);
}
.cfg-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  flex-wrap: wrap;
}
.cfg-title {
  font-size: 15.5px;
}
.cfg-mode {
  display: flex;
  gap: 8px;
}
.cfg-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 20px;
  margin-top: 16px;
}
.cfg-field {
  display: block;
}
.cfg-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cfg-input-row > :first-child {
  flex: 1;
}
.cfg-key {
  display: block;
  font-family: var(--mono);
  font-size: 12.5px;
  font-weight: 600;
  margin-bottom: 4px;
}
.cfg-db {
  font-style: normal;
  font-size: 10.5px;
  color: var(--c-primary);
  background: var(--c-primary-bg);
  border-radius: 3px;
  padding: 0 6px;
  margin-left: 6px;
}
.cfg-desc {
  display: block;
  font-size: 11px;
  color: var(--c-ink-3);
  margin-top: 3px;
}
.cfg-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 16px;
}
.llm-slots {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}
.llm-slot {
  border: 1px solid var(--c-line);
  border-radius: var(--radius);
  padding: 14px;
  background: var(--c-bg);
}
.llm-slot-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.failover-order {
  margin-left: 8px;
  color: var(--c-ink-3);
  font-size: 11px;
  font-weight: 400;
}
.test-result {
  margin-top: 12px;
  border-radius: var(--radius);
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.6;
}
.test-result.ok {
  background: #e7f2ea;
  color: #2e7d51;
  border: 1px solid #b5d4c2;
}
.test-result.bad {
  background: var(--c-pro-bg);
  color: var(--c-pro);
  border: 1px solid var(--c-pro-line);
}
.test-hint {
  margin-top: 4px;
  font-size: 11.5px;
  opacity: 0.85;
}
.mode-desc {
  font-size: 11px;
  color: var(--c-ink-3);
  margin-top: 2px;
}
.provider-status-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--c-line);
}
.provider-status-head h3 { margin: 0 0 4px; font-size: 15px; }
.provider-status-head p { margin: 0; color: var(--c-ink-3); font-size: 11.5px; }
.provider-status-head a { color: var(--c-primary); font-size: 12px; text-decoration: none; white-space: nowrap; }
.integ-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.integ-issue {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 7px 0;
  border-bottom: 1px dashed var(--c-line);
  font-size: 13px;
}
.integ-level {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  width: 60px;
}
.integ-issue.fail .integ-level { color: var(--c-pro); }
.integ-issue.warning .integ-level { color: var(--c-warn); }
.integ-kind {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--c-ink-2);
  width: 210px;
  flex-shrink: 0;
}
.integ-detail {
  color: var(--c-ink-2);
}
@media (max-width: 900px) {
  .cfg-grid {
    grid-template-columns: 1fr;
  }
}
</style>

