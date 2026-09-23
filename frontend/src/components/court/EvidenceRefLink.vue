<script setup lang="ts">
// 证据引用链接：以「类型 + 主张摘要」为主标签，完整编号降级为 tooltip / 复制按钮
import { computed } from 'vue'
import { useMessage } from 'naive-ui'
import type { EvidenceItem } from '@/types'
import { evidenceTypeLabels, stanceLabels } from '@/utils/display'

const props = withDefaults(
  defineProps<{
    evidenceId: string
    evidence?: EvidenceItem | null
    clickable?: boolean
  }>(),
  { evidence: null, clickable: false },
)

const emit = defineEmits<{ (e: 'inspect', id: string): void }>()

const message = useMessage()

const CLAIM_LIMIT = 26

const typeLabel = computed(() =>
  props.evidence ? evidenceTypeLabels[props.evidence.evidence_type] ?? props.evidence.evidence_type : '证据',
)

const claimLabel = computed(() => {
  const claim = props.evidence?.claim?.trim()
  if (!claim) return `${props.evidenceId.slice(3, 11)}…`
  return claim.length > CLAIM_LIMIT ? `${claim.slice(0, CLAIM_LIMIT)}…` : claim
})

const tooltip = computed(() => {
  const lines = [`证据编号：${props.evidenceId}`, `类型：${typeLabel.value}`]
  if (props.evidence) {
    lines.push(`立场：${stanceLabels[props.evidence.stance] ?? props.evidence.stance}`)
    lines.push(`主张：${props.evidence.claim}`)
  }
  return lines.join('\n')
})

function onInspect() {
  if (props.clickable) emit('inspect', props.evidenceId)
}

async function copyId() {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(props.evidenceId)
    message.success('证据编号已复制')
  } catch {
    message.warning('复制失败，请手动复制：' + props.evidenceId)
  }
}
</script>

<template>
  <span class="ev-ref">
    <button
      v-if="clickable"
      type="button"
      class="ref-link"
      :title="tooltip"
      :aria-label="`查看证据：${claimLabel}`"
      @click="onInspect"
    >
      <span class="ev-tag">{{ typeLabel }}</span>
      <span class="ev-claim">{{ claimLabel }}</span>
    </button>
    <span v-else class="ref-static" :title="tooltip">
      <span class="ev-tag">{{ typeLabel }}</span>
      <span class="ev-claim">{{ claimLabel }}</span>
    </span>
    <button
      type="button"
      class="ev-ref-copy"
      :title="`复制证据编号（${evidenceId}）`"
      aria-label="复制证据编号"
      @click="copyId"
    >
      ⧉
    </button>
  </span>
</template>

<style scoped>
.ev-ref {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  max-width: 100%;
  vertical-align: bottom;
}
.ref-link,
.ref-static {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 100%;
  font-family: var(--font);
  font-size: 12px;
  text-align: left;
  overflow-wrap: anywhere;
}
.ref-link {
  background: none;
  border: none;
  color: var(--c-primary);
  cursor: pointer;
  padding: 0 2px;
  border-radius: 4px;
}
.ref-link:hover .ev-claim {
  text-decoration: underline;
}
.ev-tag {
  flex: 0 0 auto;
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--c-ink-3);
  border: 1px solid var(--c-line);
  border-radius: 4px;
  padding: 0 4px;
  background: var(--c-bg);
}
.ev-claim {
  min-width: 0;
  overflow-wrap: anywhere;
}
.ev-ref-copy {
  flex: 0 0 auto;
  background: none;
  border: none;
  color: var(--c-ink-3);
  cursor: pointer;
  font-size: 11px;
  padding: 0 3px;
  border-radius: 3px;
}
.ev-ref-copy:hover {
  color: var(--c-primary);
}
.ref-link:focus-visible,
.ev-ref-copy:focus-visible {
  outline: 3px solid rgba(5, 109, 232, 0.2);
  outline-offset: 2px;
}

@media (pointer: coarse) {
  .ref-link {
    min-height: 44px;
    padding: 8px 4px;
  }
  .ev-ref-copy {
    min-width: 32px;
    min-height: 32px;
  }
}
</style>
