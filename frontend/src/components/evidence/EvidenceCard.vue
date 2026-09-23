<script setup lang="ts">
// 证据卡片：默认摘要，可原地展开全文；点击来源按钮打开完整证据侧栏
import { computed, ref } from 'vue'
import type { EvidenceItem } from '@/types'
import { evidenceTypeLabels, pct, stanceLabels } from '@/utils/display'

const props = defineProps<{ evidence: EvidenceItem; selected?: boolean }>()
const emit = defineEmits<{ (e: 'inspect', id: string): void }>()
const expanded = ref(false)

const typeClass = computed(() => `ev-type ${props.evidence.evidence_type}`)
const fallbackExtracted = computed(() =>
  props.evidence.limitations.some((item) => item.includes('摘要规则提取') || item.includes('未经 LLM')),
)
const visibleLimitations = computed(() =>
  props.evidence.limitations.filter((item) => !item.includes('摘要规则提取') && !item.includes('未经 LLM')).slice(0, 1),
)
const fullText = computed(() => props.evidence.summary || props.evidence.claim)
const displayedText = computed(() => (expanded.value ? fullText.value : props.evidence.claim))
const needsExpand = computed(() => fullText.value.length > 145 || fullText.value.length > props.evidence.claim.length + 20)
</script>

<template>
  <article class="ev-card" :class="[evidence.stance, { selected, expanded }]">
    <div class="ev-head">
      <span :class="typeClass">{{ evidenceTypeLabels[evidence.evidence_type] ?? evidence.evidence_type }}</span>
      <span class="stance" :class="evidence.stance">{{ stanceLabels[evidence.stance] ?? evidence.stance }}</span>
      <span class="strength">{{ pct(evidence.strength) }}</span>
    </div>
    <div class="claim" :class="{ expanded }">{{ displayedText }}</div>
    <button v-if="needsExpand" class="expand-btn" @click="expanded = !expanded">
      {{ expanded ? '收起内容 ↑' : '展开全文 ↓' }}
    </button>
    <div v-if="evidence.quoted_fragment" class="quote">“{{ evidence.quoted_fragment }}”</div>
    <div v-if="fallbackExtracted || visibleLimitations.length" class="limits">
      <span v-if="fallbackExtracted" class="extract-mode">摘要规则提取 · 来源真实</span>
      <span v-for="l in visibleLimitations" :key="l" class="limit-item">⚠ {{ l }}</span>
    </div>
    <button class="inspect-btn" @click="emit('inspect', evidence.id)">查看完整证据与来源 →</button>
  </article>
</template>

<style scoped>
.ev-card {
  background: #fff;
  border: 1px solid var(--c-line);
  border-radius: var(--radius);
  padding: 13px 15px 11px;
  transition: box-shadow 0.15s, border-color 0.15s;
  font-size: 13.5px;
  align-self: start;
}
.ev-card:hover { border-color: #c6c9d4; box-shadow: var(--shadow-card); }
.ev-card.selected { border-color: var(--c-primary); box-shadow: 0 0 0 2px rgba(5, 109, 232, 0.15); }
.ev-card.pro { border-left: 3px solid var(--c-pro); }
.ev-card.con { border-left: 3px solid var(--c-defense); }
.ev-head { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
.stance { font-size: 11.5px; }
.stance.pro { color: var(--c-pro); }
.stance.con { color: var(--c-defense); }
.stance.neutral { color: var(--c-ink-3); }
.strength { margin-left: auto; font-family: var(--mono); font-size: 12px; color: var(--c-ink-2); font-weight: 600; }
.claim {
  color: var(--c-ink);
  line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.claim.expanded { display: block; overflow: visible; }
.expand-btn,
.inspect-btn {
  background: none;
  border: none;
  padding: 0;
  font-family: var(--font);
  cursor: pointer;
  font-size: 11.5px;
}
.expand-btn { color: var(--c-ink-3); margin-top: 6px; }
.expand-btn:hover { color: var(--c-primary); }
.inspect-btn {
  display: block;
  width: 100%;
  text-align: left;
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px dashed var(--c-line);
  color: var(--c-primary);
}
.quote { margin-top: 7px; padding: 6px 10px; background: var(--c-bg); border-radius: 6px; font-size: 12.5px; color: var(--c-ink-3); }
.limits { margin-top: 7px; display: flex; flex-direction: column; gap: 3px; }
.limit-item { font-size: 11.5px; color: var(--c-warn); }
.extract-mode { display: inline-flex; align-self: flex-start; padding: 1px 7px; border-radius: 4px; background: #fdf7e9; color: #92701f; font-size: 11px; }
</style>
