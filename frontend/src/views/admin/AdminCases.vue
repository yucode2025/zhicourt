<script setup lang="ts">
// Admin 案件管理：筛选/搜索/分页/失败重试（二次确认）
import { onMounted, ref } from 'vue'
import { NButton, NInput, NPagination, NSelect, useDialog, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { AdminCase } from '@/types'
import { fmtTime } from '@/utils/display'
import StatusTag from '@/components/common/StatusTag.vue'
import DemoBadge from '@/components/common/DemoBadge.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'

const message = useMessage()
const dialog = useDialog()

const status = ref('')
const q = ref('')
const page = ref(1)
const size = ref(20)
const total = ref(0)
const items = ref<AdminCase[]>([])
const loading = ref(false)
const error = ref('')
const retryBusy = ref<Set<string>>(new Set())

function setRetryBusy(id: string, value: boolean) {
  const next = new Set(retryBusy.value)
  value ? next.add(id) : next.delete(id)
  retryBusy.value = next
}

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已宣判', value: 'verdict_ready' },
  { label: '运行中', value: 'running' },
  { label: '排队中', value: 'queued' },
  { label: '失败', value: 'failed' },
  { label: '待启动', value: 'created' },
]

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.adminCases(status.value, q.value, page.value, size.value)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

function confirmRetry(c: AdminCase) {
  dialog.warning({
    title: '重试失败案件',
    content: `将清空「${c.title}」的旧产出并重新开庭，会消耗一定 API 配额。确定执行？`,
    positiveText: '重新开庭',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (retryBusy.value.has(c.id)) return
      setRetryBusy(c.id, true)
      error.value = ''
      try {
        await api.adminRetryCase(c.id)
        message.success('已重新进入开庭队列')
        await load()
      } catch (e) {
        error.value = e instanceof Error ? e.message : '重试失败'
        message.error(error.value)
      } finally {
        setRetryBusy(c.id, false)
      }
    },
  })
}

onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>案件管理</h1>
      <div class="tools">
        <NInput v-model:value="q" placeholder="搜索标题 / 命题" clearable style="width: 220px" @keyup.enter="page = 1; load()" />
        <NSelect v-model:value="status" :options="statusOptions" style="width: 130px" @update:value="page = 1; load()" />
        <NButton @click="page = 1; load()">筛选</NButton>
      </div>
    </div>

    <p v-if="error" class="admin-action-error" role="alert">{{ error }}</p>
    <div class="card" style="overflow-x: auto">
      <table class="tbl">
        <thead>
          <tr>
            <th>案件</th><th>用户</th><th>状态</th><th>Engine</th>
            <th>来源/证据</th><th>创建时间</th><th>耗时</th><th style="text-align: right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8"><div class="skeleton-block" style="height: 24px; margin: 8px" /></td></tr>
          <tr v-else-if="!items.length"><td colspan="8" class="empty-state" style="padding: 28px">没有匹配的案件</td></tr>
          <tr v-for="c in items" v-else :key="c.id">
            <td style="max-width: 260px">
              <RouterLink :to="`/case/${c.id}`" class="c-title" :title="c.title">{{ c.title }}</RouterLink>
              <DemoBadge :is-demo="c.is_demo" />
              <div class="c-prop">{{ c.proposition }}</div>
            </td>
            <td>{{ c.owner }}</td>
            <td><StatusTag :status="c.status" /></td>
            <td><span class="mode-pill" :class="c.engine_mode === 'llm' ? 'REAL' : 'FALLBACK'">{{ c.engine_mode }}</span></td>
            <td class="mono">{{ c.sources }} / {{ c.evidence }}</td>
            <td class="mono dim">{{ fmtTime(c.created_at ?? '') }}</td>
            <td class="mono dim">{{ c.duration_s !== null ? `${c.duration_s}s` : '—' }}</td>
            <td style="text-align: right">
              <NButton v-if="c.status === 'failed'" size="tiny" type="warning" quaternary :loading="retryBusy.has(c.id)" :disabled="retryBusy.has(c.id)" @click="confirmRetry(c)">重试</NButton>
              <AppLinkButton v-else :to="`/case/${c.id}`" size="tiny" quaternary>查看</AppLinkButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="pager">
      <NSelect
        v-model:value="size" size="small" style="width: 110px"
        :options="[20, 50, 100].map((n) => ({ label: `${n} / 页`, value: n }))"
        @update:value="page = 1; load()"
      />
      <NPagination v-model:page="page" :page-size="size" :item-count="total" @update:page="load" />
    </div>
  </div>
</template>

<style scoped src="@/assets/admin.css" />
<style scoped>
.admin-action-error { color: var(--c-danger); font-size: 13px; margin: 0 0 12px; }
.c-title {
  font-weight: 600;
  font-size: 13.5px;
  margin-right: 6px;
  /* 提升到单元格背景之上，保证标题链接始终可点击 */
  display: inline-block;
  position: relative;
  z-index: 1;
}
.c-prop {
  font-size: 12px;
  color: var(--c-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 250px;
}
</style>
