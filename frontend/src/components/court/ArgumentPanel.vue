<script setup lang="ts">
// 控方 / 辩方论证面板：长论据可原地展开，不再无提示截断
import { NButton } from 'naive-ui'
import { computed, ref } from 'vue'
import type { ArgumentItem, EvidenceItem } from '@/types'
import ScoreWidget from '@/components/common/ScoreWidget.vue'
import EvidenceRefLink from '@/components/court/EvidenceRefLink.vue'

const props = withDefaults(
  defineProps<{
    side: 'prosecution' | 'defense'
    title: string
    subtitle: string
    argumentsList: ArgumentItem[]
    evidence: EvidenceItem[]
    /** 全量证据索引：论证可能引用中性证据，仅靠按立场过滤的 evidence 会查不到 */
    evidenceIndex?: EvidenceItem[]
    /** 只读场景（如分享页）关闭点击查看 */
    inspectable?: boolean
    /** 只读页面隐藏“我来质询”等写操作入口 */
    readonly?: boolean
  }>(),
  { evidenceIndex: () => [], inspectable: true, readonly: false },
)

const emit = defineEmits<{ (e: 'inspect', id: string): void; (e: 'challenge', id?: string): void }>()
const expandedIds = ref<Set<string>>(new Set())
const sideName = computed(() => (props.side === 'prosecution' ? '控方' : '辩方'))

// 论证引用的证据编号 → 证据对象（供引用链接展示类型与主张摘要）
const evidenceById = computed(() => {
  const map = new Map<string, EvidenceItem>()
  for (const ev of props.evidenceIndex?.length ? props.evidenceIndex : props.evidence) map.set(ev.id, ev)
  return map
})

function toggle(id: string) {
  const next = new Set(expandedIds.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expandedIds.value = next
}
</script>

<template>
  <section class="arg-panel" :class="side">
    <div class="panel-head">
      <span class="side-chip" :class="side">{{ sideName }}</span>
      <div>
        <div class="panel-title">{{ title }}</div>
        <div class="panel-sub">{{ subtitle }}</div>
      </div>
    </div>

    <div class="args">
      <article v-for="arg in argumentsList" :key="arg.id" class="arg-item">
        <div class="arg-title">{{ arg.title }} <NButton v-if="!readonly" size="tiny" quaternary @click="emit('challenge', arg.id)">质询这条论证</NButton></div>
        <div class="arg-body" :class="{ expanded: expandedIds.has(arg.id) }">{{ arg.body }}</div>
        <button v-if="arg.body.length > 170" class="expand-btn" @click="toggle(arg.id)">
          {{ expandedIds.has(arg.id) ? '收起论据 ↑' : '展开完整论据 ↓' }}
        </button>
        <div class="arg-foot">
          <ScoreWidget :value="arg.strength" :label="side === 'prosecution' ? '论证强度' : '反例强度'" :warm="side === 'defense'" />
        </div>
        <div v-if="arg.evidence_ids.length" class="arg-refs">
          引用证据：
          <EvidenceRefLink
            v-for="eid in arg.evidence_ids.slice(0, 4)"
            :key="eid"
            :evidence-id="eid"
            :evidence="evidenceById.get(eid) ?? null"
            :clickable="inspectable"
            @inspect="emit('inspect', $event)"
          />
        </div>
      </article>
      <div v-if="!argumentsList.length" class="empty-state" style="padding: 24px">暂无论证</div>
    </div>

    <div class="panel-foot">
      <span class="ev-count">{{ evidence.length }} 条{{ side === 'defense' ? '反方' : '支持' }}证据</span>
      <NButton v-if="!readonly" size="tiny" quaternary type="error" @click="emit('challenge')">我来质询</NButton>
      <span v-else class="ro-hint">公开只读</span>
    </div>
  </section>
</template>

<style scoped>
.arg-panel { background: #fff; border: 1px solid var(--c-line); border-radius: var(--radius-lg); box-shadow: var(--shadow-card); overflow: hidden; }
.arg-panel.prosecution { border-top: 4px solid var(--c-pro); }
.arg-panel.defense { border-top: 4px solid var(--c-defense); }
.panel-head { display: flex; gap: 10px; align-items: flex-start; padding: 16px 18px 12px; }
.panel-title { font-size: 15px; font-weight: 600; }
.panel-sub { font-size: 12px; color: var(--c-ink-3); margin-top: 2px; }
.args { padding: 0 18px; }
.arg-item { border-top: 1px dashed var(--c-line); padding: 12px 0; }
.arg-title { font-size: 13.5px; font-weight: 600; margin-bottom: 6px; }
.arg-body {
  font-size: 13px;
  color: var(--c-ink-2);
  line-height: 1.75;
  display: -webkit-box;
  -webkit-line-clamp: 7;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.arg-body.expanded { display: block; overflow: visible; }
.expand-btn { margin-top: 7px; padding: 0; border: none; background: none; color: var(--c-primary); font-family: var(--font); font-size: 11.5px; cursor: pointer; }
.arg-foot { margin-top: 10px; }
.arg-refs {
  margin-top: 6px;
  font-size: 12px;
  color: var(--c-ink-3);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: 2px;
  row-gap: 2px;
}
.panel-foot { display: flex; align-items: center; justify-content: space-between; padding: 10px 18px; border-top: 1px solid var(--c-line); background: var(--c-bg); }
.ev-count { font-size: 12px; color: var(--c-ink-3); }
.ro-hint { font-size: 11.5px; color: var(--c-ink-3); }
</style>
