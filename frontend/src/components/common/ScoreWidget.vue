<script setup lang="ts">
// 法官置信度与证据强度通用分数显示
defineProps<{ value: number; label?: string; warm?: boolean }>()
import { pct } from '@/utils/display'
</script>

<template>
  <div class="score-widget">
    <div class="row">
      <span class="label">{{ label ?? '证据可信度' }}</span>
      <span class="value">{{ pct(value) }}</span>
    </div>
    <div class="score-bar" :class="{ warm }">
      <div class="fill" :style="{ width: pct(value) }" />
    </div>
  </div>
</template>

<style scoped>
.score-widget .row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--c-ink-3);
  margin-bottom: 4px;
}
.score-widget .value {
  font-family: var(--mono);
  font-weight: 600;
  color: var(--c-ink-2);
}
</style>
