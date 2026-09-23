<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import type { ProviderStatus, UsageProvider } from '@/types'
import AdminChart from '@/components/admin/MiniChart.vue'

type Full = Record<string, UsageProvider & { near_limit: boolean }>
type OfficialQuotaItem = {
  APIID: string
  APIName: string
  TotalQuota: number
  TotalUsed: number
  RemainingQuota: number
}

const data = ref<{ today: string; providers: Full; history: Record<string, { date: string; calls: number; cache_hits: number; failures: number }[]> } | null>(null)
const quota = ref<OfficialQuotaItem[]>([])
const providers = ref<ProviderStatus[]>([])
const loading = ref(false)
const quotaLoading = ref(false)
const officialError = ref('')
const localError = ref('')
const refreshedAt = ref('')
const quotaCached = ref(false)
const quotaStale = ref(false)

const NAMES: Record<string, string> = {
  zhihu_search: '知乎搜索', web_search: '全网搜索', zhihu_hot: '知乎热榜',
  zhihu_direct_answer: '知乎直答', zhihu_knowledge: '知乎知识库', llm: 'LLM',
}
const MODE_NAMES: Record<string, string> = {
  REAL: '真实接口', MOCK: '演示接口', FALLBACK: '降级模式',
  DISABLED: '已关闭', NOT_USED: '未启用',
}

function pctOf(item: OfficialQuotaItem): number {
  if (item.TotalQuota <= 0) return 0
  return Math.min(1, Math.max(0, item.TotalUsed / item.TotalQuota))
}

function level(ratio: number): string {
  if (ratio >= 0.9) return 'bad'
  if (ratio >= 0.8) return 'warn'
  return ''
}

function numberText(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

const statusByKey = computed(() => new Map(providers.value.map((item) => [item.key, item])))
const localRows = computed(() => Object.entries(data.value?.providers ?? {}).map(([key, usage]) => {
  const status = statusByKey.value.get(key)
  return {
    key,
    name: status?.name ?? NAMES[key] ?? key,
    mode: status?.mode ?? 'NOT_USED',
    status: status?.status ?? 'BLOCKED',
    ...usage,
  }
}))
const localCalls = computed(() => localRows.value.reduce((sum, item) => sum + item.calls, 0))
const localFailures = computed(() => localRows.value.reduce((sum, item) => sum + item.failures, 0))
const activeProviders = computed(() => providers.value.filter((item) => item.mode === 'REAL').length)
const quotaWarnings = computed(() => quota.value.filter((item) => pctOf(item) >= 0.8).length)

const trendOption = computed(() => {
  const h = data.value?.history ?? {}
  const dates = new Set<string>()
  Object.values(h).forEach((rows) => rows.forEach((row) => dates.add(row.date)))
  const sorted = [...dates].sort().slice(-7)
  return {
    color: ['#1772e8', '#16a36a', '#7b61c9', '#d98b20', '#d45656', '#65819f'],
    grid: { left: 42, right: 18, top: 38, bottom: 26 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 11, color: '#697386' } },
    xAxis: { type: 'category', data: sorted, boundaryGap: false, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { color: '#edf0f4' } } },
    series: Object.entries(h).map(([key, rows]) => ({
      name: NAMES[key] ?? key,
      type: 'line',
      smooth: true,
      symbolSize: 6,
      lineStyle: { width: 2 },
      data: sorted.map((date) => rows.find((row) => row.date === date)?.calls ?? 0),
    })),
  }
})

async function loadOfficial() {
  quotaLoading.value = true
  officialError.value = ''
  try {
    const official = await api.adminQuota()
    if (official.available) {
      quota.value = official.items ?? []
      quotaCached.value = official.cached === true
      quotaStale.value = official.stale === true
      const updated = official.updated_at ? new Date(official.updated_at) : new Date()
      refreshedAt.value = updated.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    } else {
      officialError.value = official.reason ?? '知乎开放平台未返回额度数据'
    }
  } catch (error) {
    officialError.value = error instanceof Error ? error.message : '官方额度加载失败'
  } finally {
    quotaLoading.value = false
  }
}

async function loadLocal() {
  localError.value = ''
  try {
    data.value = await api.adminApiUsage()
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '本站调用记录加载失败'
  }
}

async function loadProviders() {
  try {
    providers.value = (await api.adminProviders()).items
  } catch {
    providers.value = []
  }
}

async function load() {
  if (loading.value) return
  loading.value = true
  await Promise.all([loadOfficial(), loadLocal(), loadProviders()])
  loading.value = false
}

onMounted(() => { void load() })
</script>

<template>
  <div class="usage-page">
    <div class="pg-head usage-head">
      <div>
        <div class="eyebrow">OPERATIONS · API</div>
        <h1>API 用量与额度</h1>
        <p class="page-subtitle">统一查看知乎官方额度、本站调用质量与近期趋势。</p>
      </div>
      <div class="head-actions">
        <RouterLink class="config-link" to="/admin/providers">管理接口配置</RouterLink>
        <button class="btn btn-primary" :disabled="loading" @click="load">
          {{ loading ? '刷新中…' : '刷新数据' }}
        </button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="card summary-card blue">
        <span>已接入真实接口</span>
        <b>{{ activeProviders }}<small> / {{ providers.length || '—' }}</small></b>
        <em>当前 Provider 状态</em>
      </div>
      <div class="card summary-card violet">
        <span>今日本站调用</span>
        <b>{{ numberText(localCalls) }}</b>
        <em>{{ data?.today || '当前自然日' }} 本地记录</em>
      </div>
      <div class="card summary-card" :class="localFailures ? 'red' : 'green'">
        <span>今日失败</span>
        <b>{{ numberText(localFailures) }}</b>
        <em>{{ localFailures ? '建议检查接口状态' : '调用运行正常' }}</em>
      </div>
      <div class="card summary-card" :class="quotaWarnings ? 'amber' : 'green'">
        <span>额度预警</span>
        <b>{{ quotaWarnings }}</b>
        <em>{{ quotaWarnings ? '已有能力使用超过 80%' : '官方额度充足' }}</em>
      </div>
    </div>

    <section class="card panel quota-panel">
      <div class="panel-head">
        <div>
          <h2>知乎官方额度</h2>
          <p>实时查询开放平台，查询本身不消耗业务额度。</p>
        </div>
        <span class="refresh-time">
          {{ quotaLoading && quota.length ? '正在刷新…' : quotaStale ? `缓存数据 · ${refreshedAt || '—'}` : quotaCached ? `缓存 · ${refreshedAt || '—'}` : `更新于 ${refreshedAt || '—'}` }}
        </span>
      </div>

      <div v-if="quotaLoading && !quota.length" class="panel-state">正在查询官方额度…</div>
      <div v-else-if="officialError" class="panel-state error-state-inline">
        <span>{{ officialError }}</span>
        <RouterLink to="/admin/providers">检查 Access Secret</RouterLink>
      </div>
      <div v-else class="quota-grid">
        <article v-for="item in quota" :key="item.APIID" class="quota-card" :class="level(pctOf(item))">
          <div class="quota-top">
            <div>
              <b>{{ item.APIName }}</b>
              <span>{{ item.APIID }}</span>
            </div>
            <strong>{{ Math.round(pctOf(item) * 100) }}%</strong>
          </div>
          <div class="remaining"><b>{{ numberText(item.RemainingQuota) }}</b><span>剩余</span></div>
          <div class="quota-bar"><div class="fill" :style="{ width: `${pctOf(item) * 100}%` }" /></div>
          <div class="quota-foot">
            <span>已用 {{ numberText(item.TotalUsed) }}</span>
            <span>总额 {{ numberText(item.TotalQuota) }}</span>
          </div>
        </article>
      </div>
    </section>

    <div class="content-grid">
      <section class="card panel local-panel">
        <div class="panel-head">
          <div>
            <h2>本站调用明细</h2>
            <p>应用自身记录，用于定位失败和缓存表现。</p>
          </div>
          <span class="scope-badge">LOCAL</span>
        </div>
        <div v-if="localError" class="panel-state error-state-inline">{{ localError }}</div>
        <div v-else class="table-wrap">
          <table class="usage-table">
            <thead><tr><th>接口</th><th>状态</th><th>调用</th><th>缓存命中</th><th>失败</th></tr></thead>
            <tbody>
              <tr v-for="item in localRows" :key="item.key">
                <td><b>{{ item.name }}</b><small>{{ MODE_NAMES[item.mode] ?? item.mode }}</small></td>
                <td><span class="status-dot" :class="item.status" />{{ item.status }}</td>
                <td class="mono">{{ item.calls }}</td>
                <td class="mono">{{ item.calls > 0 ? `${Math.round((item.cache_hits / item.calls) * 100)}%` : '—' }}</td>
                <td class="mono" :class="{ danger: item.failures > 0 }">{{ item.failures }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card panel trend-panel">
        <div class="panel-head">
          <div>
            <h2>近 7 日调用趋势</h2>
            <p>仅代表本站记录，不等同于官方额度。</p>
          </div>
        </div>
        <AdminChart v-if="data && Object.keys(data.history).length" :option="trendOption" :height="300" />
        <div v-else class="panel-state">暂无本地调用历史。</div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.usage-page { padding-bottom: 24px; }
.usage-head { align-items: flex-end; margin-bottom: 20px; }
.usage-head h1 { margin: 3px 0 5px; font-size: 25px; letter-spacing: -.02em; }
.eyebrow { color: var(--c-primary); font: 700 10.5px/1 var(--mono); letter-spacing: .14em; }
.page-subtitle { margin: 0; color: var(--c-ink-3); font-size: 13px; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.config-link { color: var(--c-primary); font-size: 12.5px; text-decoration: none; }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.summary-card { position: relative; overflow: hidden; padding: 16px 18px; border-top: 3px solid #aeb8c6; }
.summary-card::after { content: ''; position: absolute; width: 74px; height: 74px; border-radius: 50%; right: -28px; top: -28px; background: currentColor; opacity: .055; }
.summary-card > span { display: block; color: var(--c-ink-3); font-size: 12px; }
.summary-card > b { display: block; margin: 7px 0 4px; color: var(--c-ink-1); font: 700 27px/1.1 var(--mono); }
.summary-card small { color: var(--c-ink-3); font-size: 13px; font-weight: 500; }
.summary-card em { color: var(--c-ink-3); font-size: 11px; font-style: normal; }
.summary-card.blue { color: var(--c-primary); border-color: var(--c-primary); }
.summary-card.violet { color: #7259bd; border-color: #7259bd; }
.summary-card.green { color: var(--c-success); border-color: var(--c-success); }
.summary-card.amber { color: var(--c-warn); border-color: var(--c-warn); }
.summary-card.red { color: var(--c-pro); border-color: var(--c-pro); }
.panel { padding: 0; }
.panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px 14px; border-bottom: 1px solid var(--c-line); }
.panel-head h2 { margin: 0 0 4px; font-size: 15.5px; }
.panel-head p { margin: 0; color: var(--c-ink-3); font-size: 11.5px; }
.refresh-time, .scope-badge { color: var(--c-ink-3); font: 500 10.5px/1.4 var(--mono); white-space: nowrap; }
.scope-badge { padding: 3px 7px; border-radius: 4px; background: var(--c-bg); }
.quota-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 14px; }
.quota-card { padding: 13px 14px; border: 1px solid var(--c-line); border-radius: 8px; background: #fff; transition: border-color .18s, box-shadow .18s; }
.quota-card:hover { border-color: #b9c8da; box-shadow: 0 4px 14px rgba(31, 48, 71, .06); }
.quota-card.warn { border-color: #e8ce99; background: #fffdf8; }
.quota-card.bad { border-color: var(--c-pro-line); background: #fffafa; }
.quota-top { display: flex; justify-content: space-between; gap: 10px; }
.quota-top b { display: block; font-size: 13px; }
.quota-top span { display: block; margin-top: 2px; color: var(--c-ink-3); font: 10.5px/1.3 var(--mono); }
.quota-top strong { color: var(--c-ink-3); font: 600 11px/1.3 var(--mono); }
.remaining { display: flex; align-items: baseline; gap: 6px; margin: 12px 0 9px; }
.remaining b { font: 700 22px/1 var(--mono); }
.remaining span { color: var(--c-ink-3); font-size: 11px; }
.quota-bar { height: 5px; overflow: hidden; border-radius: 4px; background: #edf0f4; }
.quota-bar .fill { height: 100%; border-radius: inherit; background: linear-gradient(90deg, #77aaf0, var(--c-primary)); }
.quota-card.warn .fill { background: var(--c-warn); }
.quota-card.bad .fill { background: var(--c-pro); }
.quota-foot { display: flex; justify-content: space-between; margin-top: 7px; color: var(--c-ink-3); font: 10.5px/1.3 var(--mono); }
.content-grid { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1.05fr); gap: 16px; margin-top: 16px; }
.table-wrap { overflow-x: auto; }
.usage-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.usage-table th { padding: 10px 16px; color: var(--c-ink-3); font-size: 10.5px; font-weight: 500; text-align: left; white-space: nowrap; background: #fafbfc; }
.usage-table td { padding: 10px 16px; border-top: 1px solid var(--c-line); white-space: nowrap; }
.usage-table td:first-child b, .usage-table td:first-child small { display: block; }
.usage-table td:first-child small { margin-top: 2px; color: var(--c-ink-3); font-size: 10.5px; }
.status-dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: #aeb8c6; vertical-align: 1px; }
.status-dot.HEALTHY { background: var(--c-success); box-shadow: 0 0 0 3px rgba(35, 154, 94, .1); }
.status-dot.DEGRADED { background: var(--c-warn); }
.status-dot.DOWN { background: var(--c-pro); }
.danger { color: var(--c-pro); font-weight: 600; }
.panel-state { padding: 28px 20px; color: var(--c-ink-3); font-size: 12.5px; text-align: center; }
.error-state-inline { display: flex; justify-content: center; gap: 10px; color: var(--c-pro); }
.error-state-inline a { color: var(--c-primary); }
@media (max-width: 1100px) {
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
  .quota-grid { grid-template-columns: repeat(2, 1fr); }
  .content-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .usage-head { align-items: flex-start; }
  .head-actions { width: 100%; justify-content: space-between; }
  .summary-grid, .quota-grid { grid-template-columns: 1fr; }
}
</style>
