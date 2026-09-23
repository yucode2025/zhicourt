<script setup lang="ts">
// Admin Dashboard：运营核心指标 + 7 天趋势 + 最近错误
import { onMounted, ref } from 'vue'
import { NButton } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { AdminDashboard } from '@/types'
import AdminChart from '@/components/admin/MiniChart.vue'

const data = ref<AdminDashboard | null>(null)
const error = ref('')
const loading = ref(false)

const trendOption = ref<Record<string, unknown>>({})

function buildTrend(d: AdminDashboard) {
  return {
    grid: { left: 34, right: 12, top: 26, bottom: 24 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['案件数', '失败数'], top: 0, textStyle: { fontSize: 11 } },
    xAxis: { type: 'category', data: d.trend.map((t) => t.date), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '案件数', type: 'line', smooth: true, data: d.trend.map((t) => t.cases),
        lineStyle: { color: '#056de8', width: 2 }, itemStyle: { color: '#056de8' },
        areaStyle: { color: 'rgba(5,109,232,0.08)' },
      },
      {
        name: '失败数', type: 'line', smooth: true, data: d.trend.map((t) => t.failed),
        lineStyle: { color: '#b04a4a', width: 2 }, itemStyle: { color: '#b04a4a' },
      },
    ],
  }
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await api.adminDashboard()
    trendOption.value = buildTrend(data.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>运营概览</h1>
      <NButton size="small" :loading="loading" :disabled="loading" @click="load">刷新</NButton>
    </div>

    <div v-if="error" class="error-state card"><h3>加载失败</h3><p>{{ error }}</p><NButton :loading="loading" :disabled="loading" @click="load">重试</NButton></div>

    <template v-else-if="data">
      <div class="stat-grid">
        <div class="stat card"><span class="stat-num">{{ data.users_today }}</span><span class="stat-label">今日新用户</span></div>
        <div class="stat card"><span class="stat-num">{{ data.users_total }}</span><span class="stat-label">总用户</span></div>
        <div class="stat card"><span class="stat-num">{{ data.cases_today }}</span><span class="stat-label">今日案件</span></div>
        <div class="stat card"><span class="stat-num">{{ data.cases_total }}</span><span class="stat-label">总案件</span></div>
        <div class="stat card warn"><span class="stat-num">{{ data.cases_running }}</span><span class="stat-label">进行中</span></div>
        <div class="stat card done"><span class="stat-num">{{ data.cases_done }}</span><span class="stat-label">已宣判</span></div>
        <div class="stat card bad"><span class="stat-num">{{ data.cases_failed }}</span><span class="stat-label">失败</span></div>
        <div class="stat card"><span class="stat-num">{{ data.questions_today }}</span><span class="stat-label">今日质询</span></div>
      </div>

      <div class="duo-grid">
        <div class="card card-pad">
          <h3 class="panel-title">近 7 天案件趋势</h3>
          <AdminChart :option="trendOption" :height="240" />
        </div>
        <div class="card card-pad">
          <h3 class="panel-title">系统健康</h3>
          <div class="health-row">
            <span>缓存命中率</span>
            <b>{{ data.cache_hit_rate === null ? '暂无数据' : `${Math.round(data.cache_hit_rate * 100)}%` }}</b>
          </div>
          <div class="health-row">
            <span>失败案件（累计）</span>
            <b :style="{ color: data.cases_failed > 0 ? 'var(--c-pro)' : 'var(--c-success)' }">{{ data.cases_failed }}</b>
          </div>
          <RouterLink to="/admin/providers" class="health-link">查看 Provider 状态 →</RouterLink>
          <RouterLink to="/admin/api-usage" class="health-link">查看 API 用量 →</RouterLink>

          <h3 class="panel-title" style="margin-top: 20px">最近失败案件</h3>
          <div v-if="!data.recent_errors.length" class="hint-text">没有失败案件 🎉</div>
          <RouterLink v-for="e in data.recent_errors" :key="e.id" :to="`/case/${e.id}`" class="err-row">
            <span class="err-title" :title="e.title">{{ e.title }}</span>
            <span class="err-msg">{{ e.error }}</span>
          </RouterLink>
        </div>
      </div>
    </template>
    <div v-else class="skeleton-block" style="height: 300px" />
  </div>
</template>

<style scoped>
.pg-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}
.pg-head h1 {
  font-size: 21px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 12px;
  margin-bottom: 18px;
}
.stat {
  padding: 14px 8px;
  text-align: center;
}
.stat-num {
  display: block;
  font-size: 24px;
  font-weight: 700;
  font-family: var(--mono);
}
.stat-label {
  font-size: 12px;
  color: var(--c-ink-3);
}
.stat.warn .stat-num { color: var(--c-warn); }
.stat.done .stat-num { color: var(--c-success); }
.stat.bad .stat-num { color: var(--c-pro); }

.duo-grid {
  display: grid;
  grid-template-columns: 1.5fr 1fr;
  gap: 16px;
}
.panel-title {
  font-size: 14.5px;
  margin-bottom: 12px;
}
.health-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px dashed var(--c-line);
  font-size: 13.5px;
  color: var(--c-ink-2);
}
.health-row b {
  font-family: var(--mono);
}
.health-link {
  display: block;
  font-size: 13px;
  padding: 8px 0 0;
}
.err-row {
  display: block;
  padding: 8px 0;
  border-bottom: 1px dashed var(--c-line);
}
.err-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--c-ink);
  display: block;
}
.err-msg {
  font-size: 12px;
  color: var(--c-pro);
}

@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(4, 1fr); }
  .duo-grid { grid-template-columns: 1fr; }
}
</style>
