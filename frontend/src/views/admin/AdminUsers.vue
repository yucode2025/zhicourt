<script setup lang="ts">
// Admin 用户管理：搜索 / 分页 / 启用禁用 / 角色（后端强制权限）
import { onMounted, ref } from 'vue'
import { NButton, NInput, NPagination, NSelect, useDialog, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { AdminUser } from '@/types'
import { fmtTime } from '@/utils/display'

const message = useMessage()
const dialog = useDialog()

const q = ref('')
const page = ref(1)
const size = ref(20)
const total = ref(0)
const items = ref<AdminUser[]>([])
const loading = ref(false)
const error = ref('')
const actionBusy = ref<Set<string>>(new Set())

function userLabel(user: AdminUser): string {
  return user.username ? `@${user.username}` : `${user.nickname || '知乎用户'}（知乎账号）`
}

function setActionBusy(id: string, value: boolean) {
  const next = new Set(actionBusy.value)
  value ? next.add(id) : next.delete(id)
  actionBusy.value = next
}

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.adminUsers(q.value, page.value, size.value)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

function confirmToggle(u: AdminUser) {
  const disabling = u.status === 'active'
  dialog.warning({
    title: disabling ? '禁用用户' : '启用用户',
    content: disabling
      ? `确定禁用 ${userLabel(u)}？该用户将无法登录。`
      : `确定重新启用 ${userLabel(u)}？`,
    positiveText: disabling ? '确定禁用' : '确定启用',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (actionBusy.value.has(u.id)) return
      setActionBusy(u.id, true)
      error.value = ''
      try {
        await api.adminSetUserStatus(u.id, disabling ? 'disabled' : 'active')
        u.status = disabling ? 'disabled' : 'active'
        message.success('已更新')
      } catch (e) {
        error.value = e instanceof Error ? e.message : '操作失败'
        message.error(error.value)
      } finally {
        setActionBusy(u.id, false)
      }
    },
  })
}

function confirmRole(u: AdminUser, role: 'user' | 'admin') {
  if (u.role === role) return
  dialog.warning({
    title: '修改角色',
    content: `将 ${userLabel(u)} 的角色改为「${role === 'admin' ? '管理员' : '普通用户'}」？`,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (actionBusy.value.has(u.id)) return
      setActionBusy(u.id, true)
      error.value = ''
      try {
        await api.adminSetUserRole(u.id, role)
        u.role = role
        message.success('角色已更新')
      } catch (e) {
        error.value = e instanceof Error ? e.message : '操作失败'
        message.error(error.value)
      } finally {
        setActionBusy(u.id, false)
      }
    },
  })
}

onMounted(load)
</script>

<template>
  <div>
    <div class="pg-head">
      <h1>用户管理</h1>
      <div class="tools">
        <NInput v-model:value="q" placeholder="搜索用户名 / 昵称" clearable style="width: 220px" @keyup.enter="page = 1; load()" />
        <NButton @click="page = 1; load()">搜索</NButton>
      </div>
    </div>

    <p v-if="error" class="admin-action-error" role="alert">{{ error }}</p>
    <div class="card" style="overflow-x: auto">
      <table class="tbl">
        <thead>
          <tr><th>用户</th><th>角色</th><th>案件</th><th>注册时间</th><th>最近登录</th><th>状态</th><th style="text-align: right">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="7"><div class="skeleton-block" style="height: 24px; margin: 8px" /></td></tr>
          <tr v-else-if="!items.length"><td colspan="7" class="empty-state" style="padding: 28px">没有匹配的用户</td></tr>
          <tr v-for="u in items" v-else :key="u.id" :class="{ dimmed: u.status === 'disabled' }">
            <td>
              <span class="u-avatar">{{ u.avatar }}</span>
              <b>{{ u.nickname }}</b>
              <span class="u-name">{{ u.username ? `@${u.username}` : '知乎账号' }}</span>
            </td>
            <td>
              <NSelect
                :value="u.role" size="tiny" :disabled="actionBusy.has(u.id)" :loading="actionBusy.has(u.id)" :options="[{ label: '用户', value: 'user' }, { label: '管理员', value: 'admin' }]"
                style="width: 96px" @update:value="(v: string) => confirmRole(u, v as 'user' | 'admin')"
              />
            </td>
            <td class="mono">{{ u.case_count }}</td>
            <td class="mono dim">{{ fmtTime(u.created_at ?? '') }}</td>
            <td class="mono dim">{{ fmtTime(u.last_login_at ?? '') }}</td>
            <td>
              <span class="stage-tag" :style="u.status === 'active' ? 'color: var(--c-success)' : 'color: var(--c-pro)'">
                {{ u.status === 'active' ? '正常' : '已禁用' }}
              </span>
            </td>
            <td style="text-align: right">
              <NButton size="tiny" :type="u.status === 'active' ? 'warning' : 'success'" quaternary :loading="actionBusy.has(u.id)" :disabled="actionBusy.has(u.id)" @click="confirmToggle(u)">
                {{ u.status === 'active' ? '禁用' : '启用' }}
              </NButton>
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
</style>
