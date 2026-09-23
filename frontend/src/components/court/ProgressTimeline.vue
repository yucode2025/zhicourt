<script setup lang="ts">
// Agent 实时进度时间线
import { computed } from 'vue'
import type { ProgressEvent } from '@/types'

const props = defineProps<{ events: ProgressEvent[] }>()

const stageNames: Record<string, string> = {
  init: '受理', plan: '规划', research: '检索', rank: '筛选',
  evidence: '取证', debate: '对抗', cross_exam: '质证', judge: '审理', verdict: '判决', done: '完成',
}

const latest = computed(() => (props.events.length ? props.events[props.events.length - 1].i : -1))
</script>

<template>
  <div v-if="!events.length" class="empty-state" style="padding: 24px">
    正在等待庭审事件……
  </div>
  <div v-else class="progress-line">
    <div v-for="e in events" :key="e.i" class="progress-item" :class="{ latest: e.i === latest }">
      <span v-if="stageNames[e.stage]" class="stage-name">[{{ stageNames[e.stage] }}]</span>
      {{ e.message }}
    </div>
  </div>
</template>

<style scoped>
.stage-name {
  color: var(--c-ink-3);
  font-size: 12px;
  margin-right: 4px;
}
</style>
