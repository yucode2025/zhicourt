<script setup lang="ts">
// 403 页面仅提供前端提示；真正的权限由后端强制。
import CourtClerk from '@/components/common/CourtClerk.vue'
</script>

<template>
  <main class="page err-page">
    <div class="err-card card">
      <CourtClerk variant="ball" :size="130" />
      <div class="err-code">403</div>
      <h1>无权访问该区域</h1>
      <p>该区域仅管理员可见。如果你认为这是错误，请联系系统管理员。</p>
      <RouterLink to="/" class="btn primary" style="text-decoration: none">返回首页</RouterLink>
    </div>
  </main>
</template>

<style scoped>
.err-page {
  display: flex;
  justify-content: center;
  padding-top: 60px;
}
.err-card {
  text-align: center;
  padding: 46px 80px 50px;
}
.err-code {
  font-family: var(--mono);
  font-size: 52px;
  font-weight: 800;
  color: var(--c-line);
  -webkit-text-stroke: 2px var(--c-warn);
  margin: 4px 0 6px;
}
h1 {
  font-size: 22px;
  margin-bottom: 8px;
}
p {
  color: var(--c-ink-3);
  margin: 0 0 22px;
}
</style>
