<script setup lang="ts">
import { computed } from 'vue'
import type { CaseStatus } from '@/types'

const props = defineProps<{ status: CaseStatus; stage?: string }>()

const map: Record<CaseStatus, { text: string; color: string }> = {
  created: { text: '待启动', color: '#787a85' },
  queued: { text: '排队等待开庭', color: '#b07d2b' },
  running: { text: '审理中', color: '#056de8' },
  verdict_ready: { text: '已宣判', color: '#2e7d51' },
  failed: { text: '审理失败', color: '#b04a4a' },
}
const info = computed(() => map[props.status] ?? map.created)
</script>

<template>
  <span class="stage-tag" :style="{ color: info.color, borderColor: info.color + '55' }">
    <span
      :style="{
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: info.color,
        animation: status === 'running' ? 'shimmer 1s infinite' : undefined,
      }"
    />
    {{ info.text }}
  </span>
</template>
