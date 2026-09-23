<script setup lang="ts">
import CourtClerk from '@/components/common/CourtClerk.vue'
</script>

<template>
  <main class="page err-page">
    <div class="err-card card">
      <CourtClerk variant="wander" :size="130" />
      <div class="err-code">404</div>
      <h1>页面走失了</h1>
      <p>你要找的页面可能已被移动、删除，或从未存在。</p>
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
  -webkit-text-stroke: 2px var(--c-primary);
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
