<script setup lang="ts">
// 管理后台布局：左侧 Sidebar + 内容区（视觉与前台同一设计系统）
import { useRoute } from 'vue-router'

const route = useRoute()
const nav = [
  { path: '/admin', label: '概览', icon: '◱', exact: true },
  { path: '/admin/users', label: '用户', icon: '👤' },
  { path: '/admin/cases', label: '案件', icon: '⚖' },
  { path: '/admin/api-usage', label: 'API 用量', icon: '◷' },
  { path: '/admin/providers', label: 'Provider', icon: '⛁' },
  { path: '/admin/audit', label: '审计日志', icon: '☰' },
  { path: '/admin/settings', label: '设置', icon: '⚙' },
]
</script>

<template>
  <div class="admin-shell">
    <aside class="admin-side">
      <RouterLink to="/" class="side-brand">
        <span class="logo">Z</span> ZhiCourt <em>运营后台</em>
      </RouterLink>
      <nav class="side-nav">
        <RouterLink
          v-for="n in nav"
          :key="n.path"
          :to="n.path"
          class="side-link"
          :class="{ active: n.exact ? route.path === n.path : route.path.startsWith(n.path) }"
        >
          <span class="icon">{{ n.icon }}</span>{{ n.label }}
        </RouterLink>
      </nav>
      <div class="side-foot">
        <RouterLink to="/">← 返回前台</RouterLink>
      </div>
    </aside>
    <main class="admin-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  min-height: calc(100vh - 58px);
}
.admin-side {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid var(--c-line);
  background: #fff;
  padding: 20px 12px;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 58px;
  height: calc(100vh - 58px);
}
.side-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  color: var(--c-ink);
  padding: 0 10px 18px;
  border-bottom: 1px solid var(--c-line);
  margin-bottom: 14px;
}
.side-brand .logo {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: var(--c-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}
.side-brand em {
  font-style: normal;
  font-size: 11px;
  font-weight: 400;
  color: var(--c-ink-3);
}
.side-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.side-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 7px;
  color: var(--c-ink-2);
  font-size: 13.5px;
}
.side-link .icon {
  width: 18px;
  text-align: center;
  opacity: 0.75;
}
.side-link:hover {
  background: var(--c-bg);
}
.side-link.active {
  background: var(--c-primary-bg);
  color: var(--c-primary);
  font-weight: 500;
}
.side-foot {
  margin-top: auto;
  padding: 10px;
  font-size: 12.5px;
}
.admin-main {
  flex: 1;
  padding: 26px 30px 60px;
  min-width: 0;
}
@media (max-width: 900px) {
  .admin-shell {
    flex-direction: column;
  }
  .admin-side {
    width: 100%;
    height: auto;
    position: static;
    flex-direction: row;
    align-items: center;
    overflow-x: auto;
    padding: 10px 14px;
  }
  .side-brand {
    border: none;
    margin: 0;
    padding: 0 10px;
    white-space: nowrap;
  }
  .side-brand em {
    display: none;
  }
  .side-nav {
    flex-direction: row;
  }
  .side-link {
    white-space: nowrap;
  }
  .side-foot {
    display: none;
  }
  .admin-main {
    padding: 18px 14px 50px;
  }
}
</style>
