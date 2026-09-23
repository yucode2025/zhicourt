<script setup lang="ts">
import { NDropdown, type DropdownOption, useMessage } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppLinkButton from '@/components/common/AppLinkButton.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const mobileOpen = ref(false)
const logoutBusy = ref(false)

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') mobileOpen.value = false
}

onMounted(() => {
  document.addEventListener('keydown', onDocumentKeydown)
  void auth.fetchMe()
})
onBeforeUnmount(() => document.removeEventListener('keydown', onDocumentKeydown))
watch(() => route.fullPath, () => { mobileOpen.value = false })

const userOptions = computed(() => {
  const opts: DropdownOption[] = [
    { label: '用户中心', key: 'profile' },
    { label: '我的案件', key: 'cases' },
    { label: '我的收藏', key: 'favorites' },
  ]
  if (auth.isAdmin) opts.push({ label: '管理后台', key: 'admin' })
  opts.push({ label: logoutBusy.value ? '正在退出…' : '退出登录', key: 'logout', disabled: logoutBusy.value })
  return opts
})

async function onUserAction(key: string) {
  if (key === 'logout') {
    if (logoutBusy.value) return
    logoutBusy.value = true
    try {
      await auth.logout()
      await router.push('/')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '退出失败，请稍后重试')
    } finally {
      logoutBusy.value = false
    }
    return
  }
  const targets: Record<string, string> = {
    admin: '/admin', cases: '/cases', favorites: '/profile?tab=favorites', profile: '/profile',
  }
  await router.push(targets[key] ?? '/profile')
}

const mobileOptions = computed(() => {
  const opts: DropdownOption[] = [
    { label: '首页', key: 'home' },
    { label: '历史案件', key: 'cases' },
    { label: '发起审理', key: 'new' },
  ]
  if (auth.isLoggedIn) {
    opts.push(
      { label: '用户中心', key: 'profile' },
      { label: '我的收藏', key: 'favorites' },
    )
    if (auth.isAdmin) opts.push({ label: '管理后台', key: 'admin' })
    opts.push({ label: logoutBusy.value ? '正在退出…' : '退出登录', key: 'logout', disabled: logoutBusy.value })
  } else {
    opts.push({ label: '登录', key: 'login' }, { label: '注册', key: 'register' })
  }
  return opts
})

async function onMobileAction(key: string) {
  mobileOpen.value = false
  if (key === 'logout') return onUserAction(key)
  const targets: Record<string, string> = {
    home: '/', cases: '/cases', new: '/case/new', profile: '/profile', favorites: '/profile?tab=favorites',
    admin: '/admin', login: '/login', register: '/register',
  }
  await router.push(targets[key] ?? '/')
}
</script>

<template>
  <header class="app-header">
    <div class="inner">
      <RouterLink to="/" class="brand" aria-label="ZhiCourt 知识法庭首页">
        <span class="logo" aria-hidden="true">Z</span>
        <span>ZhiCourt</span>
        <small>知识法庭 · 让观点接受证据审理</small>
      </RouterLink>
      <nav class="nav-links" aria-label="主导航">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/cases">历史案件</RouterLink>
      </nav>

      <div class="desktop-actions">
        <template v-if="auth.isLoggedIn">
          <AppLinkButton to="/case/new" type="primary" size="small" round>发起审理</AppLinkButton>
          <NDropdown :options="userOptions" @select="onUserAction" trigger="click">
            <button type="button" class="avatar-btn" :title="auth.user?.nickname" aria-label="打开用户菜单" aria-haspopup="menu">
              <span class="avatar" aria-hidden="true">{{ auth.user?.avatar }}</span>
              <span class="avatar-name">{{ auth.user?.nickname }}</span>
            </button>
          </NDropdown>
        </template>
        <template v-else>
          <RouterLink to="/login" class="ghost-link" :class="{ active: route.name === 'login' }">登录</RouterLink>
          <AppLinkButton to="/register" size="small" round>注册</AppLinkButton>
          <AppLinkButton to="/case/new" type="primary" size="small" round>发起审理</AppLinkButton>
        </template>
      </div>

      <div class="mobile-actions">
        <button
          type="button"
          class="mobile-menu-btn"
          aria-label="主导航菜单"
          aria-haspopup="menu"
          aria-controls="mobile-navigation"
          :aria-expanded="mobileOpen"
          @click="mobileOpen = !mobileOpen"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
        <nav v-if="mobileOpen" id="mobile-navigation" class="mobile-menu" role="menu" aria-label="移动端主导航">
          <button
            v-for="option in mobileOptions"
            :key="String(option.key)"
            type="button"
            role="menuitem"
            :disabled="Boolean(option.disabled)"
            @click="onMobileAction(String(option.key))"
          >{{ option.label }}</button>
        </nav>
      </div>
    </div>
  </header>
</template>

<style scoped>
.desktop-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.mobile-actions { display: none; margin-left: auto; position: relative; }
.avatar-btn { display: flex; align-items: center; gap: 7px; background: none; border: 1px solid transparent; border-radius: 20px; padding: 3px 10px 3px 4px; cursor: pointer; font-family: var(--font); }
.avatar-btn:hover { background: var(--c-bg); border-color: var(--c-line); }
.avatar { width: 28px; height: 28px; border-radius: 50%; background: var(--c-primary-bg); display: flex; align-items: center; justify-content: center; font-size: 15px; }
.avatar-name { font-size: 13.5px; color: var(--c-ink-2); max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ghost-link { font-size: 13.5px; color: var(--c-ink-2); padding: 5px 10px; border-radius: 6px; }
.ghost-link.active, .ghost-link:hover { color: var(--c-primary); background: var(--c-primary-bg); }
.mobile-menu-btn { display: inline-flex; align-items: center; justify-content: center; background: #fff; border: 1px solid var(--c-line); border-radius: 8px; padding: 7px; cursor: pointer; color: var(--c-ink-2); }
.mobile-menu { position: absolute; top: calc(100% + 9px); right: 0; width: min(240px, calc(100vw - 28px)); padding: 6px; background: #fff; border: 1px solid var(--c-line); border-radius: 9px; box-shadow: var(--shadow-float); display: flex; flex-direction: column; }
.mobile-menu button { width: 100%; padding: 9px 12px; border: 0; border-radius: 6px; background: transparent; color: var(--c-ink-2); font: inherit; font-size: 13.5px; text-align: left; cursor: pointer; }
.mobile-menu button:hover, .mobile-menu button:focus-visible { color: var(--c-primary); background: var(--c-primary-bg); }
.mobile-menu button:disabled { opacity: .55; cursor: wait; }
@media (max-width: 640px) {
  .desktop-actions, .nav-links { display: none; }
  .mobile-actions { display: block; }
  .brand { min-width: 0; }
  .brand small { display: none; }
}
</style>
