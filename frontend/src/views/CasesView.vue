<script setup lang="ts">
// 历史案件：查看 / 筛选 / 搜索 / 继续 / 打开判决书
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NInput, NSelect } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { CaseSummary } from '@/types'
import { fmtTime } from '@/utils/display'
import StatusTag from '@/components/common/StatusTag.vue'
import DemoBadge from '@/components/common/DemoBadge.vue'
import CourtClerk from '@/components/common/CourtClerk.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'

const route = useRoute()

const cases = ref<CaseSummary[]>([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const statusFilter = ref<string>('')

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已宣判', value: 'verdict_ready' },
  { label: '审理中', value: 'running' },
  { label: '排队中', value: 'queued' },
  { label: '失败', value: 'failed' },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    cases.value = await api.listCases(keyword.value, statusFilter.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

// URL ?status= 是筛选的权威来源（概览卡片跳转依赖它）
function syncFromQuery() {
  const q = route.query.status
  statusFilter.value = typeof q === 'string' && q ? q : ''
}
watch(
  () => route.query.status,
  () => {
    syncFromQuery()
    void load()
  },
)

onMounted(() => {
  syncFromQuery()
  void load()
})
</script>

<template>
  <main class="page">
    <div class="cases-head">
      <div>
        <h1 class="page-title">历史案件</h1>
        <p class="page-sub">查看、筛选并继续你的所有知识审理案件。</p>
      </div>
      <AppLinkButton to="/case/new" type="primary">发起审理</AppLinkButton>
    </div>

    <div class="filter-row">
      <NInput v-model:value="keyword" placeholder="搜索案件标题或命题…" clearable @keyup.enter="load" style="max-width: 320px" />
      <NSelect v-model:value="statusFilter" :options="statusOptions" style="width: 140px" @update:value="load" />
      <NButton @click="load">搜索</NButton>
    </div>

    <div v-if="loading" class="case-grid">
      <div v-for="i in 6" :key="i" class="skeleton-block" style="height: 130px" />
    </div>

    <div v-else-if="error" class="error-state card">
      <h3>加载失败</h3>
      <p>{{ error }}</p>
      <NButton @click="load">重试</NButton>
    </div>

    <div v-else-if="!cases.length" class="empty-state card" style="margin-top: 20px">
      <CourtClerk variant="sleepy" :size="110" caption="还没有案件" />
      <h3 style="margin-top: 10px">开启第一场知识审理</h3>
      <p>输入一个有争议的问题，让观点接受证据审理。</p>
      <AppLinkButton to="/case/new" type="primary">发起审理</AppLinkButton>
    </div>

    <div v-else class="case-grid">
      <RouterLink v-for="c in cases" :key="c.id" :to="`/case/${c.id}`" class="case-item card">
        <div class="ci-top">
          <span class="ci-title" :title="c.title">{{ c.title }}</span>
          <DemoBadge :is-demo="c.is_demo" />
        </div>
        <div class="ci-prop">{{ c.proposition }}</div>
        <div class="ci-foot">
          <StatusTag :status="c.status" />
          <span class="ci-time">{{ fmtTime(c.created_at) }}</span>
          <span v-if="c.status === 'verdict_ready'" class="ci-verdict-link">打开判决书 →</span>
        </div>
      </RouterLink>
    </div>
  </main>
</template>

<style scoped>
.page-title {
  font-size: 26px;
}
.page-sub {
  color: var(--c-ink-3);
  margin: 6px 0 0;
}
.cases-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 22px;
}
.filter-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}
.case-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}
.case-item {
  display: block;
  padding: 16px 18px;
  color: var(--c-ink);
  transition: box-shadow 0.15s, transform 0.15s;
}
.case-item:hover {
  box-shadow: var(--shadow-float);
  transform: translateY(-2px);
}
.ci-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.ci-title {
  font-weight: 600;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ci-prop {
  font-size: 13px;
  color: var(--c-ink-3);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 42px;
}
.ci-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.ci-time {
  font-size: 12px;
  color: var(--c-ink-3);
}
.ci-verdict-link {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--c-primary);
  font-weight: 500;
}
</style>
