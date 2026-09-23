<script setup lang="ts">
// 庭审现场：案件头 + 三栏（控方 | Judge | 辩方）+ 证据/来源 + 实时进度 + 质询
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NDrawer, NDrawerContent, NTabPane, NTabs, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import { useCaseStore } from '@/stores/case'
import type { EvidenceItem } from '@/types'
import { conclusionStanceLabels, fmtTime, issueTypeLabels } from '@/utils/display'
import StatusTag from '@/components/common/StatusTag.vue'
import DemoBadge from '@/components/common/DemoBadge.vue'
import ScoreWidget from '@/components/common/ScoreWidget.vue'
import EvidenceCard from '@/components/evidence/EvidenceCard.vue'
import SourceCard from '@/components/source/SourceCard.vue'
import ArgumentPanel from '@/components/court/ArgumentPanel.vue'
import ProgressTimeline from '@/components/court/ProgressTimeline.vue'
import ChallengeDialog from '@/components/court/ChallengeDialog.vue'
import EvidenceRefLink from '@/components/court/EvidenceRefLink.vue'
import CourtClerk from '@/components/common/CourtClerk.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'
import { graphEntryAvailable } from '@/utils/evidenceGraph'

const route = useRoute()
const store = useCaseStore()
const message = useMessage()

const caseId = computed(() => route.params.id as string)
const kase = computed(() => store.current)

const proArgs = computed(() => kase.value?.arguments.filter((a) => a.side === 'prosecution') ?? [])
const defArgs = computed(() => kase.value?.arguments.filter((a) => a.side === 'defense') ?? [])
const proEvidence = computed(() => kase.value?.evidence.filter((e) => e.stance === 'pro') ?? [])
const defEvidence = computed(() => kase.value?.evidence.filter((e) => e.stance === 'con') ?? [])
const evidenceRun = computed(() => kase.value?.agent_runs.find((r) => r.agent === 'evidence') ?? null)
const evidenceModeInfo = computed(() => {
  const mode = evidenceRun.value?.mode
  const sourceMode = kase.value?.source_mode
  if (sourceMode === 'mock') {
    return {
      tone: 'warn',
      label: 'Evidence Agent · 演示提取',
      text: '当前仅有演示来源，系统只展示结构化提取流程；这些内容不构成现实证据。',
    }
  }
  if (mode === 'llm') return { tone: 'ok', label: 'Evidence Agent · LLM 精读完成', text: '全部证据已由 LLM 基于可追溯来源摘要进行结构化提取。' }
  if (mode === 'mixed') return { tone: 'warn', label: 'Evidence Agent · 混合提取', text: '部分来源完成 LLM 精读；其余批次已按来源摘要规则提取，请逐条核验。' }
  return { tone: 'warn', label: 'Evidence Agent · 规则提取', text: '本案未成功使用 LLM 精读，证据按可追溯来源摘要规则提取，请逐条核验。' }
})

const drawer = ref(false)
const inspectEvidence = ref<EvidenceItem | null>(null)
let drawerTrigger: HTMLElement | null = null
const challengeAfterDrawer = ref(false)
// 移动端（≤640px）下抽屉宽度不超出视口，避免内容被裁剪
const viewportWidth = ref(window.innerWidth)
const drawerWidth = computed(() => Math.min(440, viewportWidth.value))
function onViewportResize() {
  viewportWidth.value = window.innerWidth
}
const challengeOpen = ref(false)
const challengeRefId = ref<string | undefined>()
function openChallenge(target: typeof challengeTarget.value, refId?: string) {
  challengeTarget.value = target
  challengeRefId.value = refId
  challengeOpen.value = true
}
const trialBusy = ref<'start' | 'retry' | null>(null)
const actionError = ref('')
const judgeExpanded = ref(false)
const challengeTarget = ref<'prosecution' | 'defense' | 'judge' | 'evidence' | 'source'>('judge')
const graphAvailable = computed(() => graphEntryAvailable(kase.value))

function openEvidence(id: string) {
  drawerTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  inspectEvidence.value = kase.value?.evidence.find((e) => e.id === id) ?? null
  drawer.value = !!inspectEvidence.value
}

function afterDrawerLeave() {
  inspectEvidence.value = null
  const target = drawerTrigger
  drawerTrigger = null
  if (challengeAfterDrawer.value) {
    challengeAfterDrawer.value = false
    if (target?.isConnected) target.focus()
    challengeOpen.value = true
  } else if (target?.isConnected) {
    target.focus()
  }
}

async function copyEvidenceId() {
  if (!inspectEvidence.value) return
  try {
    await navigator.clipboard.writeText(inspectEvidence.value.id)
    message.success('证据编号已复制')
  } catch {
    message.warning('复制失败，请手动复制：' + inspectEvidence.value.id)
  }
}

async function startTrial() {
  if (!kase.value || trialBusy.value) return
  trialBusy.value = 'start'
  actionError.value = ''
  try {
    await api.startCase(kase.value.id)
    message.success('案件已进入开庭队列')
    await store.load(kase.value.id, true)
    store.startLive(kase.value.id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '启动失败'
    message.error(actionError.value)
  } finally {
    trialBusy.value = null
  }
}

async function retryTrial() {
  if (!kase.value || trialBusy.value) return
  trialBusy.value = 'retry'
  actionError.value = ''
  try {
    await api.retryCase(kase.value.id)
    message.success('已清理旧产出并重新排队')
    await store.load(kase.value.id, true)
    store.startLive(kase.value.id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '重新审理失败'
    message.error(actionError.value)
  } finally {
    trialBusy.value = null
  }
}

// 切换案件（同路由参数变化）：清空旧实体并关闭弹层后重新加载
watch(caseId, async (id) => {
  drawer.value = false
  challengeOpen.value = false
  await store.load(id)
  if (store.isRunning) store.startLive(id)
})

onMounted(() => {
  window.addEventListener('resize', onViewportResize)
  void store.load(caseId.value).then(() => {
    if (store.isRunning) store.startLive(caseId.value)
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onViewportResize)
  store.stopLive()
})

watch(
  () => store.current?.status,
  (s, old) => {
    if (s === 'verdict_ready' && old && old !== 'verdict_ready') {
      message.success('审理完成，判决书已生成')
    }
  },
)
</script>

<template>
  <main class="page">
    <!-- 加载中 -->
    <div v-if="store.loading && !kase" class="skeleton-page">
      <div class="skeleton-block" style="height: 34px; width: 50%; margin-bottom: 16px" />
      <div class="skeleton-block" style="height: 90px; margin-bottom: 20px" />
      <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px">
        <div v-for="i in 3" :key="i" class="skeleton-block" style="height: 320px" />
      </div>
    </div>

    <!-- 错误 -->
    <div v-else-if="store.error && !kase" class="error-state card" style="margin-top: 40px">
      <h3>案件加载失败</h3>
      <p>{{ store.error }}</p>
      <NButton type="primary" @click="store.load(caseId)">重试</NButton>
    </div>

    <template v-else-if="kase">
      <!-- 案件头 -->
      <div class="case-head">
        <div class="head-left">
          <h1 class="case-title" :title="kase.title">{{ kase.title }}</h1>
          <div class="case-meta">
            <StatusTag :status="kase.status" />
            <DemoBadge :is-demo="kase.is_demo" />
            <span v-if="kase.execution_summary?.engine_resolved === false" class="mode-note pending">审理模式待判定</span>
            <span v-else-if="kase.engine_mode === 'heuristic'" class="mode-note">启发式审理（LLM 不可用或已降级）</span>
            <span v-else-if="kase.engine_mode === 'mixed'" class="mode-note">混合审理（部分步骤已降级）</span>
            <span class="time">{{ fmtTime(kase.created_at) }}</span>
          </div>
          <div class="proposition-box">
            <span class="prop-label">审理命题</span>
            <span class="prop-text">{{ kase.proposition }}</span>
          </div>
        </div>
        <div class="head-actions">
          <AppLinkButton v-if="graphAvailable" :to="`/case/${kase.id}/evidence`" size="small">证据图谱</AppLinkButton>
          <AppLinkButton v-if="kase.verdict" :to="`/case/${kase.id}/verdict`" size="small" type="primary">《知识判决书》</AppLinkButton>
        </div>
      </div>

      <!-- 排队提示 -->
      <div v-if="kase.status === 'queued'" class="card card-pad queue-banner clerk-banner">
        <CourtClerk variant="idle" :size="72" />
        <div class="clerk-text">
          <b>案件正在等待开庭</b>
          <span>前方还有其他案件在审理，系统正在排队调度……</span>
        </div>
      </div>

      <!-- 失败提示 -->
      <div v-else-if="kase.status === 'failed'" class="card card-pad fail-banner">
        <div>审理未能完成：{{ kase.error_message ?? '未知错误' }}</div>
        <NButton size="small" type="primary" :loading="trialBusy === 'retry'" :disabled="trialBusy !== null" @click="retryTrial">重新开庭</NButton>
      </div>

      <!-- 未开庭：created 状态给出明确入口 -->
      <div v-else-if="kase.status === 'created'" class="card card-pad queue-banner clerk-banner">
        <CourtClerk variant="greet" :size="72" />
        <div class="clerk-text">
          <b>案件已创建，还未开庭</b>
          <span>确认审理命题后点击开始，检索与控辩审将自动进行。</span>
        </div>
        <NButton size="small" type="primary" :loading="trialBusy === 'start'" :disabled="trialBusy !== null" @click="startTrial">开始审理</NButton>
      </div>

      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>

      <!-- 运行中：进度 + 空状态 -->
      <div v-if="kase.status === 'queued' || kase.status === 'running'" class="run-grid">
        <div class="card card-pad run-progress clerk-panel">
          <div class="clerk-corner">
            <CourtClerk variant="computer" :size="84" caption="系统正在记录庭审" />
          </div>
          <h3 class="panel-title">Agent 实时工作状态</h3>
          <ProgressTimeline :events="store.liveEvents.length ? store.liveEvents : kase.progress_events" />
        </div>
        <div class="run-sides">
          <div class="card card-pad"><div class="skeleton-line" style="width: 60%" /><div class="skeleton-line" style="width: 90%" /><div class="skeleton-line" style="width: 75%" /></div>
          <div class="card card-pad"><div class="skeleton-line" style="width: 70%" /><div class="skeleton-line" style="width: 55%" /><div class="skeleton-line" style="width: 85%" /></div>
        </div>
      </div>

      <!-- 已完成：判决摘要 + 控辩双栏 -->
      <template v-if="kase.status === 'verdict_ready' && kase.verdict">
        <section class="card judge-summary-panel">
          <div class="judge-summary-main">
            <div class="judge-head">
              <span class="side-chip court">Judge · 当前判断</span>
              <span class="stance-label">{{ conclusionStanceLabels[kase.verdict.conclusion_stance] }}</span>
            </div>
            <div class="judge-conclusion">{{ kase.verdict.conclusion }}</div>
            <ScoreWidget :value="kase.verdict.confidence" label="判决置信度（反映证据强度，并非绝对真理）" warm />
            <div class="judge-actions">
              <AppLinkButton :to="`/case/${kase.id}/verdict`" type="primary" size="small">打开完整判决书</AppLinkButton>
              <NButton size="small" @click="judgeExpanded = !judgeExpanded">{{ judgeExpanded ? '收起摘要' : '展开全部摘要' }}</NButton>
              <NButton size="small" @click="openChallenge('judge', kase.verdict.id)">质询 Judge</NButton>
            </div>
          </div>
          <div class="judge-summary-grid" :class="{ expanded: judgeExpanded }">
            <div class="judge-fact-box">
              <div class="js-title">双方共同认可</div>
              <ul><li v-for="f in (judgeExpanded ? kase.verdict.shared_facts : kase.verdict.shared_facts.slice(0, 2))" :key="f" :title="f">{{ f }}</li></ul>
            </div>
            <div class="judge-fact-box">
              <div class="js-title">核心分歧</div>
              <ul><li v-for="f in (judgeExpanded ? kase.verdict.core_disputes : kase.verdict.core_disputes.slice(0, 2))" :key="f" :title="f">{{ f }}</li></ul>
            </div>
            <div class="judge-fact-box unknown">
              <div class="js-title">仍无法确定</div>
              <ul><li v-for="f in (judgeExpanded ? kase.verdict.unknowns : kase.verdict.unknowns.slice(0, 2))" :key="f" :title="f">{{ f }}</li></ul>
            </div>
          </div>
        </section>

        <div class="debate-grid">
          <ArgumentPanel
            side="prosecution"
            title="控方（Prosecutor）"
            subtitle="支持命题的最强论证"
            :arguments-list="proArgs"
            :evidence="proEvidence"
            :evidence-index="kase.evidence"
            @inspect="openEvidence"
            @challenge="openChallenge('prosecution', $event)"
          />
          <ArgumentPanel
            side="defense"
            title="辩方（Defense）"
            subtitle="反对命题的最强论证与反例"
            :arguments-list="defArgs"
            :evidence="defEvidence"
            :evidence-index="kase.evidence"
            @inspect="openEvidence"
            @challenge="openChallenge('defense', $event)"
          />
        </div>

        <!-- 质证 + 进度 -->
        <div class="bottom-grid">
          <div class="card card-pad">
            <h3 class="panel-title">Cross Examiner 质证发现</h3>
            <div v-if="!kase.cross_examinations.length" class="empty-state" style="padding: 20px">未发现明显问题。</div>
            <div v-for="cx in kase.cross_examinations" :key="cx.id" class="cx-item">
              <div class="cx-head">
                <span class="cx-target" :class="cx.target_side">{{ cx.target_side === 'prosecution' ? '控方' : cx.target_side === 'defense' ? '辩方' : '双方' }}</span>
                <span class="cx-type">{{ issueTypeLabels[cx.issue_type] ?? cx.issue_type }}</span>
                <span class="cx-sev" :class="cx.severity">{{ cx.severity === 'high' ? '高' : cx.severity === 'medium' ? '中' : '低' }}</span>
              </div>
              <div class="cx-desc">{{ cx.description }}</div>
              <div v-if="cx.related_evidence_ids.length" class="cx-refs">
                关联证据：
                <EvidenceRefLink
                  v-for="eid in cx.related_evidence_ids"
                  :key="eid"
                  :evidence-id="eid"
                  :evidence="kase.evidence.find((e) => e.id === eid) ?? null"
                  clickable
                  @inspect="openEvidence"
                />
              </div>
            </div>
          </div>

          <div class="card card-pad">
            <h3 class="panel-title">审理过程</h3>
            <ProgressTimeline :events="kase.progress_events" />
          </div>
        </div>

        <!-- 证据 / 来源 Tabs -->
        <div class="card card-pad" style="margin-top: 18px">
          <NTabs type="line" animated>
            <NTabPane name="evidence" :tab="`证据（${kase.evidence.length}）`">
              <div class="evidence-mode-notice" :class="evidenceModeInfo.tone">
                <b>{{ evidenceModeInfo.label }}</b>
                <span>{{ evidenceModeInfo.text }}</span>
              </div>
              <div v-if="kase.evidence.length" class="ev-grid">
                <EvidenceCard
                  v-for="ev in kase.evidence"
                  :key="ev.id"
                  :evidence="ev"
                  :selected="inspectEvidence?.id === ev.id"
                  @inspect="openEvidence"
                />
              </div>
              <div v-else class="empty-state"><div class="icon">◈</div>暂无证据</div>
            </NTabPane>
            <NTabPane name="sources" :tab="`来源（${kase.sources.length}）`">
              <div class="src-grid">
                <div v-for="s in kase.sources" :key="s.id"><SourceCard :source="s" /><NButton size="tiny" @click="openChallenge('source', s.id)">质询此来源</NButton></div>
              </div>
            </NTabPane>
            <NTabPane v-if="kase.user_questions.length" name="challenges" :tab="`我的质询（${kase.user_questions.length}）`">
              <div v-for="uq in kase.user_questions" :key="uq.id" class="uq-item">
                <div class="uq-q">「{{ uq.target }}」{{ uq.text }}</div>
                <div class="uq-type">分类：{{ uq.challenge_type }}</div>
                <div class="uq-a">{{ uq.response }}</div>
              </div>
            </NTabPane>
          </NTabs>
        </div>
      </template>

      <!-- 证据侧栏 -->
      <NDrawer
        v-model:show="drawer"
        :width="drawerWidth"
        placement="right"
        :mask-closable="true"
        :close-on-esc="true"
        @after-leave="afterDrawerLeave"
      >
        <NDrawerContent v-if="inspectEvidence" title="证据详情" closable>
          <div class="ev-detail">
            <ScoreWidget :value="inspectEvidence.strength" />
            <div class="jd-block jd-id-block">
              <div class="jd-label">证据编号</div>
              <div class="jd-id-row">
                <span class="jd-id">{{ inspectEvidence.id }}</span>
                <button type="button" class="jd-id-copy" @click="copyEvidenceId">复制编号</button>
              </div>
            </div>
            <div class="jd-block">
              <div class="jd-label">证据主张</div>
              <div>{{ inspectEvidence.claim }}</div>
            </div>
            <div v-if="inspectEvidence.summary" class="jd-block">
              <div class="jd-label">内容摘要</div>
              <div>{{ inspectEvidence.summary }}</div>
            </div>
            <div v-if="inspectEvidence.quoted_fragment" class="jd-block">
              <div class="jd-label">原文引用</div>
              <div class="jd-quote">“{{ inspectEvidence.quoted_fragment }}”</div>
            </div>
            <div v-if="inspectEvidence.limitations.length" class="jd-block">
              <div class="jd-label">已知局限</div>
              <ul><li v-for="l in inspectEvidence.limitations" :key="l">{{ l }}</li></ul>
            </div>
            <div class="jd-block">
              <div class="jd-label">来源</div>
              <SourceCard
                v-if="kase.sources.find((s) => s.id === inspectEvidence!.source_id)"
                :source="kase.sources.find((s) => s.id === inspectEvidence!.source_id)!"
              />
            </div>
            <div class="jd-block">
              <NButton size="small" @click="challengeRefId = inspectEvidence?.id; challengeTarget = 'evidence'; challengeAfterDrawer = true; drawer = false">
                质疑这条证据
              </NButton>
            </div>
          </div>
        </NDrawerContent>
      </NDrawer>

      <!-- 质询对话框 -->
      <ChallengeDialog
        v-model:show="challengeOpen"
        :case-id="kase.id"
        :target="challengeTarget"
        :target-ref-id="challengeRefId"
        @submitted="store.load(kase.id, true)"
      />
    </template>
  </main>
</template>

<style scoped>
.action-error { color: var(--c-danger); font-size: 13px; margin: -8px 0 14px; }
.case-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 22px;
}
.case-title {
  font-size: 26px;
  margin-bottom: 8px;
}
.case-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.mode-note {
  font-size: 12px;
  color: var(--c-warn);
}
.mode-note.pending {
  color: var(--c-ink-3);
}
.time {
  font-size: 12.5px;
  color: var(--c-ink-3);
}
.proposition-box {
  background: #fff;
  border: 1px solid var(--c-line);
  border-left: 4px solid var(--c-primary);
  border-radius: var(--radius);
  padding: 12px 16px;
  display: flex;
  align-items: baseline;
  gap: 12px;
  max-width: 760px;
}
.prop-label {
  font-size: 12px;
  color: var(--c-primary);
  font-weight: 600;
  flex-shrink: 0;
}
.prop-text {
  font-size: 15.5px;
  font-weight: 600;
  line-height: 1.5;
}
.head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.queue-banner,
.fail-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--c-ink-2);
  margin-bottom: 18px;
  justify-content: space-between;
}
.queue-banner {
  border-left: 4px solid var(--c-warn);
  justify-content: flex-start;
  gap: 16px;
}
.clerk-banner .clerk-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.clerk-banner .clerk-text b {
  font-size: 14.5px;
}
.clerk-banner .clerk-text span {
  font-size: 13px;
  color: var(--c-ink-3);
}
.clerk-panel {
  position: relative;
  overflow: hidden;
}
.clerk-corner {
  position: absolute;
  right: 8px;
  top: 6px;
  opacity: 0.95;
  text-align: center;
}
.clerk-corner :deep(figcaption) {
  font-size: 10.5px !important;
}
.fail-banner {
  border-left: 4px solid var(--c-danger);
}
.pulse-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--c-warn);
  animation: pulse 1.2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.run-grid {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 18px;
}
.run-sides {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-content: start;
}
.skeleton-line {
  height: 14px;
  border-radius: 4px;
  margin-bottom: 12px;
  background: linear-gradient(90deg, #eef0f3 25%, #f6f7f9 50%, #eef0f3 75%);
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}

.judge-summary-panel {
  display: grid;
  grid-template-columns: minmax(360px, .9fr) minmax(0, 1.8fr);
  gap: 20px;
  padding: 20px 22px;
  border-top: 4px solid var(--c-judge);
  margin-bottom: 18px;
}
.judge-summary-main {
  padding-right: 20px;
  border-right: 1px dashed var(--c-judge-line);
}
.judge-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.stance-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-judge);
  white-space: nowrap;
}
.judge-conclusion {
  font-size: 14.5px;
  line-height: 1.75;
  margin-bottom: 14px;
}
.judge-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.judge-fact-box {
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--c-judge-bg);
  border: 1px solid var(--c-judge-line);
  min-width: 0;
  max-height: 220px;
  overflow: hidden;
}
.judge-summary-grid.expanded .judge-fact-box { max-height: none; overflow: visible; }
.judge-summary-grid.expanded .judge-fact-box li { display: list-item; overflow: visible; }
.judge-fact-box.unknown {
  background: #faf7ee;
}
.js-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--c-judge);
  margin-bottom: 6px;
}
.judge-fact-box ul {
  margin: 0;
  padding-left: 17px;
  font-size: 12.5px;
  color: var(--c-ink-2);
  line-height: 1.65;
}
.judge-fact-box li {
  margin-bottom: 5px;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.judge-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.debate-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-bottom: 18px;
  align-items: start;
}

.bottom-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 18px;
}
.panel-title {
  font-size: 15px;
  margin-bottom: 14px;
}

.cx-item {
  border-bottom: 1px solid var(--c-line);
  padding: 10px 0;
}
.cx-item:last-child {
  border-bottom: none;
}
.cx-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 5px;
}
.cx-target {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 4px;
}
.cx-target.prosecution {
  background: var(--c-pro-bg);
  color: var(--c-pro);
}
.cx-target.defense {
  background: var(--c-defense-bg);
  color: var(--c-defense);
}
.cx-type {
  font-size: 12.5px;
  font-weight: 600;
}
.cx-sev {
  margin-left: auto;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
  background: var(--c-bg);
  color: var(--c-ink-3);
}
.cx-sev.high {
  background: var(--c-pro-bg);
  color: var(--c-pro);
}
.cx-desc {
  font-size: 13px;
  color: var(--c-ink-2);
  line-height: 1.65;
}
.cx-refs {
  margin-top: 6px;
  font-size: 12px;
  color: var(--c-ink-3);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: 4px;
  row-gap: 2px;
}
.jd-id-block .jd-id-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.jd-id {
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--c-ink-3);
  overflow-wrap: anywhere;
}
.jd-id-copy {
  flex: 0 0 auto;
  background: none;
  border: 1px solid var(--c-line);
  border-radius: 5px;
  color: var(--c-primary);
  cursor: pointer;
  font-family: var(--font);
  font-size: 11.5px;
  padding: 2px 8px;
}
.jd-id-copy:hover {
  border-color: var(--c-primary);
  background: var(--c-primary-bg);
}
.jd-id-copy:focus-visible {
  outline: 3px solid rgba(5, 109, 232, 0.2);
  outline-offset: 2px;
}

.evidence-mode-notice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 7px;
  margin: 2px 0 12px;
  font-size: 12.5px;
  line-height: 1.55;
}
.evidence-mode-notice b { white-space: nowrap; }
.evidence-mode-notice span { color: var(--c-ink-2); }
.evidence-mode-notice.ok { background: #edf7f1; border: 1px solid #bfdccb; color: var(--c-success); }
.evidence-mode-notice.warn { background: #fdf7e9; border: 1px solid #edd8a7; color: var(--c-warn); }

.ev-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
.src-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.uq-item {
  border-bottom: 1px solid var(--c-line);
  padding: 12px 0;
}
.uq-q {
  font-weight: 600;
  font-size: 13.5px;
  margin-bottom: 4px;
}
.uq-type {
  font-size: 12px;
  color: var(--c-ink-3);
  margin-bottom: 6px;
}
.uq-a {
  font-size: 13.5px;
  color: var(--c-ink-2);
  line-height: 1.7;
  white-space: pre-wrap;
}

.ev-detail .score-widget {
  margin-bottom: 14px;
}
.jd-block {
  margin-bottom: 14px;
  font-size: 13.5px;
  line-height: 1.7;
}
.jd-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-ink-3);
  margin-bottom: 5px;
}
.jd-quote {
  background: var(--c-bg);
  border-radius: 6px;
  padding: 8px 12px;
  color: var(--c-ink-2);
}

@media (max-width: 1024px) {
  .judge-summary-panel,
  .debate-grid,
  .bottom-grid,
  .run-grid {
    grid-template-columns: 1fr;
  }
  .judge-summary-main { border-right: none; border-bottom: 1px dashed var(--c-judge-line); padding: 0 0 16px; }
  .judge-summary-grid { grid-template-columns: 1fr; }
  .run-sides {
    grid-template-columns: 1fr;
  }
  .head-actions {
    flex-direction: column;
  }
}
</style>
