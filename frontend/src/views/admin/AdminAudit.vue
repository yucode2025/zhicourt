<script setup lang="ts">
// 审计日志：分页浏览管理员操作
import { onMounted, ref } from 'vue'
import { NPagination } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { AuditItem } from '@/types'
import { fmtTime } from '@/utils/display'

const page = ref(1)
const size = ref(20)
const total = ref(0)
const items = ref<AuditItem[]>([])
const loading = ref(false)
const error = ref('')

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.adminAudit(page.value, size.value)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '审计日志加载失败'
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>审计日志</h1>
      <span class="dim" style="font-size: 12.5px">记录管理员敏感操作（不含任何 Secret）</span>
    </div>
    <p v-if="error" class="admin-action-error" role="alert">{{ error }}</p>
    <div class="card" style="overflow-x: auto">
      <table class="tbl">
        <thead>
          <tr><th>时间</th><th>管理员</th><th>动作</th><th>目标</th><th>详情</th><th>IP</th><th>结果</th></tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="7"><div class="skeleton-block" style="height: 24px; margin: 8px" /></td></tr>
          <tr v-else-if="!items.length"><td colspan="7" class="empty-state" style="padding: 28px">暂无审计记录</td></tr>
          <tr v-for="a in items" v-else :key="a.id">
            <td class="mono dim">{{ fmtTime(a.created_at ?? '') }}</td>
            <td class="mono">{{ a.admin.slice(0, 12) }}</td>
            <td><b style="font-size: 12.5px">{{ a.action }}</b></td>
            <td class="mono dim">{{ a.target }}</td>
            <td class="dim">{{ a.detail }}</td>
            <td class="mono dim">{{ a.ip }}</td>
            <td>
              <span class="status-pill" :class="a.result === 'ok' ? 'HEALTHY' : 'DOWN'">{{ a.result }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="pager">
      <span class="dim" style="font-size: 12.5px">共 {{ total }} 条</span>
      <NPagination v-model:page="page" :page-size="size" :item-count="total" :disabled="loading" @update:page="load" />
    </div>
  </div>
</template>

<style scoped src="@/assets/admin.css" />
<style scoped>
.admin-action-error { color: var(--c-danger); font-size: 13px; margin: 0 0 12px; }
</style>
