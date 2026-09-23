<script setup lang="ts">
// 知识判决书：适合截图 / 演示 / 分享
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NPopconfirm, useDialog, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import type { CaseDetail } from '@/types'
import { conclusionStanceLabels, fmtTime } from '@/utils/display'
import DemoBadge from '@/components/common/DemoBadge.vue'
import ScoreWidget from '@/components/common/ScoreWidget.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EvidenceRefLink from '@/components/court/EvidenceRefLink.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'
import { graphEntryAvailable } from '@/utils/evidenceGraph'
import { shareOrCopy } from '@/utils/clipboard'

const route = useRoute()
const $router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const dialog = useDialog()
const kase = ref<CaseDetail | null>(null)
const loading = ref(true)
const error = ref('')
const isFavorite = ref(false)
const favoriteBroken = ref(false)
const favoriteBusy = ref(false)
const publishBusy = ref(false)
const shareBusy = ref(false)
const retryBusy = ref(false)
const deleteBusy = ref(false)
const actionError = ref('')
const manualShareUrl = ref('')

const caseId = computed(() => route.params.id as string)
const hasVerdict = computed(() => kase.value != null && kase.value.verdict != null)
const sourceDisclaimer = computed(() => {
  if (kase.value?.source_mode === 'mock') {
    return '本判决书当前只使用明确标注的演示来源，用于展示审理结构，不构成现实事实或倾向性判断。'
  }
  if (kase.value?.source_mode === 'mixed') {
    return '本判决书包含真实与演示来源；演示来源不参与现实裁决，事实性结论应通过证据图谱逐条核验。'
  }
  return '本判决书基于可追溯公开来源的结构化证据生成，置信度反映证据强度而非客观真理。'
})

const shareUrl = computed(() =>
  kase.value?.public_id ? `${window.location.origin}/share/${kase.value.public_id}` : '',
)

async function copyShare() {
  if (!shareUrl.value || shareBusy.value) return
  shareBusy.value = true
  actionError.value = ''
  manualShareUrl.value = ''
  try {
    const result = await shareOrCopy(shareUrl.value, kase.value?.title ?? 'ZhiCourt 知识判决书')
    if (result === 'share') message.success('已打开系统分享')
    else if (result === 'clipboard') message.success('分享链接已复制')
    else {
      manualShareUrl.value = shareUrl.value
      actionError.value = '自动复制不可用，请从下方文本框手动复制链接。'
    }
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) {
      manualShareUrl.value = shareUrl.value
      actionError.value = '分享失败，请从下方文本框手动复制链接。'
    }
  } finally {
    shareBusy.value = false
  }
}

async function toggleFavorite() {
  if (!kase.value || favoriteBusy.value) return
  favoriteBusy.value = true
  actionError.value = ''
  try {
    const res = isFavorite.value ? await api.unfavorite(caseId.value) : await api.favorite(caseId.value)
    isFavorite.value = res.is_favorite
    favoriteBroken.value = false
    message.success(res.is_favorite ? '已收藏判决书' : '已取消收藏')
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '收藏失败'
    message.error(actionError.value)
  } finally {
    favoriteBusy.value = false
  }
}

async function togglePublish() {
  if (!kase.value || publishBusy.value) return
  publishBusy.value = true
  actionError.value = ''
  const before = kase.value
  try {
    const updated = kase.value.is_public ? await api.unpublishCase(kase.value.id) : await api.publishCase(kase.value.id)
    // 发布/撤回接口返回摘要；与当前详情合并，避免清空正文数据
    kase.value = { ...before, ...updated }
    message.success(kase.value.is_public ? '已公开，可分享链接' : '已转为私有，分享链接已失效')
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '公开状态更新失败'
    message.error(actionError.value)
  } finally {
    publishBusy.value = false
  }
}

function confirmDelete() {
  if (!kase.value) return
  dialog.warning({
    title: '删除案件',
    content: '将永久删除该案件及全部来源、证据、论证、判决书与收藏记录，且不可恢复。确定删除？',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (deleteBusy.value) return
      deleteBusy.value = true
      actionError.value = ''
      try {
        await api.deleteCase(kase.value!.id)
        message.success('案件已删除')
        await $router.push('/cases')
      } catch (e) {
        actionError.value = e instanceof Error ? e.message : '删除失败'
        message.error(actionError.value)
      } finally {
        deleteBusy.value = false
      }
    },
  })
}

async function loadDetail() {
  loading.value = true
  error.value = ''
  isFavorite.value = false
  favoriteBroken.value = false
  kase.value = null
  try {
    kase.value = await api.getCase(caseId.value)
  } catch (e) {
    if (kase.value === null) error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
  // 收藏状态独立加载：失败只影响收藏按钮，不遮蔽判决书正文
  try {
    isFavorite.value = (await api.favoriteStatus(caseId.value)).is_favorite
  } catch {
    favoriteBroken.value = true
  }
}

watch(caseId, loadDetail, { immediate: true })

async function retryCase() {
  if (!kase.value || retryBusy.value) return
  retryBusy.value = true
  actionError.value = ''
  try {
    await api.retryCase(kase.value.id)
    message.success('已重新排队，正在审理')
    await $router.push(`/case/${kase.value.id}`)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '重试失败'
    message.error(actionError.value)
  } finally {
    retryBusy.value = false
  }
}

const stanceClass = computed(() => {
  const s = kase.value?.verdict?.conclusion_stance
  return s === 'prosecution' ? 'pro' : s === 'defense' ? 'con' : 'court'
})
</script>

<template>
  <main class="page page-narrow">
    <div v-if="loading" class="skeleton-page">
      <div class="skeleton-block" style="height: 40px; width: 60%; margin: 0 auto 16px" />
      <div class="skeleton-block" style="height: 420px" />
    </div>

    <div v-else-if="error" class="error-state card">
      <h3>判决书加载失败</h3>
      <p>{{ error }}</p>
      <NButton type="primary" @click="loadDetail">重试</NButton>
    </div>

    <!-- 未生成判决：按案件状态给出明确指引，不渲染空判决书 -->
    <div v-else-if="kase && !hasVerdict" class="error-state card">
      <StatusTag :status="kase.status" />
      <h3 style="margin: 10px 0 6px">判决书尚未生成</h3>
      <p v-if="kase.status === 'created'">案件还未开庭，先回到庭审现场开始审理。</p>
      <p v-else-if="kase.status === 'queued' || kase.status === 'running'">案件正在审理中，判决书将在宣判后生成。</p>
      <p v-else-if="kase.status === 'failed'">{{ kase.error_message || '审理失败，可尝试重新审理。' }}</p>
      <p v-else>数据不一致：案件已标记完成但缺少判决书，请联系管理员。</p>
      <div class="doc-actions" style="justify-content: center">
        <AppLinkButton v-if="kase.status !== 'failed'" :to="`/case/${kase.id}`" type="primary">返回庭审现场</AppLinkButton>
        <NButton v-else type="warning" :loading="retryBusy" :disabled="retryBusy" @click="retryCase">重新审理</NButton>
      </div>
    </div>

    <template v-else-if="kase">
      <div class="doc-actions">
        <AppLinkButton :to="`/case/${kase.id}`" size="small">← 返回庭审现场</AppLinkButton>
        <AppLinkButton v-if="graphEntryAvailable(kase)" :to="`/case/${kase.id}/evidence`" size="small">证据图谱</AppLinkButton>
        <template v-if="kase.is_owner && auth.isLoggedIn">
          <NButton size="small" :type="kase.is_public ? 'warning' : 'default'" :loading="publishBusy" :disabled="publishBusy" @click="togglePublish">
            {{ kase.is_public ? '转为私有' : '公开判决' }}
          </NButton>
          <NPopconfirm @positive-click="confirmDelete">
            <template #trigger><NButton size="small" type="error" ghost :loading="deleteBusy" :disabled="deleteBusy">删除案件</NButton></template>
            删除后所有来源、证据、论证与判决书都会永久移除，确定吗？
          </NPopconfirm>
        </template>
        <NButton
          v-if="kase.is_owner || kase.is_public"
          size="small"
          :type="isFavorite ? 'warning' : 'default'"
          :loading="favoriteBusy"
          :disabled="favoriteBusy || favoriteBroken"
          @click="toggleFavorite"
        >
          {{ isFavorite ? '⭐ 已收藏' : '☆ 收藏判决' }}
        </NButton>
        <NButton v-if="kase.is_public && shareUrl" size="small" type="primary" :loading="shareBusy" :disabled="shareBusy" @click="copyShare">分享判决书</NButton>
        <span v-if="favoriteBroken" class="doc-hint error-hint" role="status">收藏状态暂不可用</span>
        <span v-else-if="kase.is_owner && !kase.is_public" class="doc-hint">
          {{ auth.isLoggedIn ? '案件默认私有，公开后可分享' : '游客案件注册后可公开分享与删除' }}
        </span>
      </div>
      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <label v-if="manualShareUrl" class="manual-share">
        <span>可复制分享链接</span>
        <input class="text-input" :value="manualShareUrl" readonly @focus="($event.target as HTMLInputElement).select()" />
      </label>

      <!-- 判决书正文 -->
      <article class="verdict-doc card" :class="stanceClass" data-testid="verdict-doc">
        <header class="vd-header">
          <div class="vd-brand">ZhiCourt · 知识法庭</div>
          <h1>知识判决书</h1>
          <div class="vd-sub" :title="kase.title">《{{ kase.title }}》</div>
          <div class="vd-meta">
            <DemoBadge :is-demo="kase.is_demo" />
            <span>立案时间 {{ fmtTime(kase.created_at) }}</span>
            <span>判决模式：{{ kase.engine_mode === 'llm' ? 'LLM 审理' : kase.engine_mode === 'mixed' ? '混合审理（部分步骤降级）' : '启发式审理' }}</span>
          </div>
        </header>

        <section class="vd-proposition">
          <div class="vd-label">审理命题</div>
          <div class="vd-prop">{{ kase.proposition }}</div>
        </section>

        <section class="vd-verdict" :class="stanceClass">
          <div class="vd-label">当前较合理结论</div>
          <div class="vd-conclusion">
            <span class="vd-stance" :class="stanceClass">{{ conclusionStanceLabels[kase.verdict?.conclusion_stance ?? 'conditional'] }}</span>
            {{ kase.verdict?.conclusion }}
          </div>
          <ScoreWidget :value="kase.verdict?.confidence ?? 0" label="判决置信度（非绝对真理）" warm />
        </section>

        <section class="vd-two-col">
          <div class="vd-side pro">
            <div class="vd-side-title">控方核心观点</div>
            <p>{{ kase.verdict?.prosecution_summary || '（见庭审页控方论证）' }}</p>
          </div>
          <div class="vd-side con">
            <div class="vd-side-title">辩方核心观点</div>
            <p>{{ kase.verdict?.defense_summary || '（见庭审页辩方论证）' }}</p>
          </div>
        </section>

        <section class="vd-section">
          <h3>双方共同认可的事实</h3>
          <ul><li v-for="f in kase.verdict?.shared_facts" :key="f">{{ f }}</li></ul>
        </section>

        <section class="vd-section">
          <h3>核心分歧</h3>
          <ul><li v-for="f in kase.verdict?.core_disputes" :key="f">{{ f }}</li></ul>
        </section>

        <div class="vd-two-col">
          <section class="vd-section">
            <h3>最强支持证据</h3>
            <ul class="ev-refs">
              <li v-for="eid in kase.verdict?.strongest_evidence_ids" :key="eid">
                <EvidenceRefLink :evidence-id="eid" :evidence="kase.evidence.find((e) => e.id === eid) ?? null" />
              </li>
              <li v-if="!kase.verdict?.strongest_evidence_ids.length" class="ev-refs-empty">本判决未列出关键支持证据</li>
            </ul>
          </section>
          <section class="vd-section">
            <h3>最强反例</h3>
            <ul class="ev-refs">
              <li v-for="eid in kase.verdict?.strongest_counter_evidence_ids" :key="eid">
                <EvidenceRefLink :evidence-id="eid" :evidence="kase.evidence.find((e) => e.id === eid) ?? null" />
              </li>
              <li v-if="!kase.verdict?.strongest_counter_evidence_ids.length" class="ev-refs-empty">本判决未列出关键反例证据</li>
            </ul>
          </section>
        </div>

        <div class="vd-two-col">
          <section class="vd-section">
            <h3>证据缺口</h3>
            <ul><li v-for="f in kase.verdict?.evidence_gaps" :key="f">{{ f }}</li></ul>
          </section>
          <section class="vd-section">
            <h3>概念定义冲突</h3>
            <ul><li v-for="f in kase.verdict?.definition_conflicts" :key="f">{{ f }}</li></ul>
          </section>
        </div>

        <section class="vd-section">
          <h3>当前无法确定的问题</h3>
          <ul class="unknown"><li v-for="f in kase.verdict?.unknowns" :key="f">{{ f }}</li></ul>
        </section>

        <section class="vd-section">
          <h3>哪些新证据可能改变当前判断</h3>
          <ul><li v-for="f in kase.verdict?.verdict_changers" :key="f">{{ f }}</li></ul>
        </section>

        <section class="vd-section">
          <h3>质证综述</h3>
          <p class="cx-summary">{{ kase.verdict?.cross_exam_summary }}</p>
        </section>

        <section class="vd-section next">
          <h3>推荐继续学习的问题</h3>
          <div class="next-questions">
            <button v-for="q in kase.verdict?.next_questions" :key="q" class="next-q" @click="$router.push({ path: '/case/new', query: { q } })">
              {{ q }} →
            </button>
          </div>
        </section>

        <footer class="vd-footer">
          <div class="vd-clerk">
            <span>本判决书由系统生成并留存审理记录</span>
          </div>
          <div class="vd-status-row">
            <StatusTag :status="kase.status" />
            <span>证据 {{ kase.evidence.length }} 条 · 来源 {{ kase.sources.length }} 个 · 质证发现 {{ kase.cross_examinations.length }} 项</span>
          </div>
          <p class="vd-disclaimer">
            {{ sourceDisclaimer }}
            标注“AI 推断”的内容为系统推断，仍需独立验证。
          </p>
        </footer>
      </article>
    </template>
  </main>
</template>

<style scoped>
.doc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  min-width: 0;
}
.doc-actions > * {
  min-width: 0;
}
.doc-hint {
  font-size: 12px;
  color: var(--c-ink-3);
  align-self: center;
}
.error-hint, .action-error { color: var(--c-danger); }
.action-error { margin: -6px 0 12px; font-size: 13px; }
.manual-share { display: block; margin: -4px 0 14px; font-size: 12px; color: var(--c-ink-3); }
.manual-share .text-input { margin-top: 5px; padding: 8px 10px; font-size: 12px; }
.verdict-doc,
.verdict-doc * {
  overflow-wrap: anywhere;
}
.verdict-doc {
  padding: 44px 52px;
  border-top: 6px solid var(--c-judge);
}
.verdict-doc.pro {
  border-top-color: var(--c-pro);
}
.verdict-doc.con {
  border-top-color: var(--c-defense);
}

.vd-header {
  text-align: center;
  margin-bottom: 26px;
}
.vd-brand {
  font-size: 12.5px;
  letter-spacing: 2px;
  color: var(--c-ink-3);
  margin-bottom: 10px;
}
.vd-header h1 {
  font-size: 32px;
  letter-spacing: 6px;
}
.vd-sub {
  font-size: 15px;
  color: var(--c-ink-2);
  margin-top: 8px;
}
.vd-meta {
  display: flex;
  gap: 14px;
  justify-content: center;
  font-size: 12px;
  color: var(--c-ink-3);
  margin-top: 12px;
  flex-wrap: wrap;
}

.vd-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--c-judge);
  margin-bottom: 6px;
  letter-spacing: 1px;
}
.vd-proposition {
  text-align: center;
  border: 1px dashed var(--c-judge-line);
  border-radius: var(--radius);
  padding: 14px 20px;
  margin-bottom: 24px;
}
.vd-prop {
  font-size: 17px;
  font-weight: 600;
}

.vd-verdict {
  background: var(--c-judge-bg);
  border: 1px solid var(--c-judge-line);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  margin-bottom: 24px;
}
.vd-verdict.pro {
  background: var(--c-pro-bg);
  border-color: var(--c-pro-line);
}
.vd-verdict.pro .vd-label {
  color: var(--c-pro);
}
.vd-verdict.con {
  background: var(--c-defense-bg);
  border-color: var(--c-defense-line);
}
.vd-verdict.con .vd-label {
  color: var(--c-defense);
}
.vd-conclusion {
  font-size: 15px;
  line-height: 1.8;
  margin-bottom: 14px;
}
.vd-stance {
  display: inline-block;
  font-size: 12px;
  font-weight: 700;
  border-radius: 4px;
  padding: 1px 8px;
  margin-right: 8px;
  background: var(--c-judge-line);
  color: #6d5f3c;
}
.vd-stance.pro {
  background: var(--c-pro);
  color: #fff;
}
.vd-stance.con {
  background: var(--c-defense);
  color: #fff;
}

.vd-two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.vd-side {
  border-radius: var(--radius);
  padding: 14px 18px;
  font-size: 13.5px;
  line-height: 1.7;
}
.vd-side.pro {
  background: var(--c-pro-bg);
  border: 1px solid var(--c-pro-line);
}
.vd-side.con {
  background: var(--c-defense-bg);
  border: 1px solid var(--c-defense-line);
}
.vd-side-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
}
.vd-side.pro .vd-side-title {
  color: var(--c-pro);
}
.vd-side.con .vd-side-title {
  color: var(--c-defense);
}
.vd-side p {
  margin: 0;
  color: var(--c-ink-2);
}

.vd-section {
  border-top: 1px dashed var(--c-line);
  padding: 18px 0 0;
  margin-top: 18px;
}
.vd-section h3 {
  font-size: 14.5px;
  margin-bottom: 10px;
}
.vd-section ul {
  margin: 0;
  padding-left: 18px;
  font-size: 13.5px;
  color: var(--c-ink-2);
  line-height: 1.8;
}
.vd-section li {
  margin-bottom: 4px;
}
.ev-refs {
  font-size: 12.5px !important;
}
.ev-refs-empty {
  color: var(--c-ink-3);
  font-size: 12.5px;
}
.unknown li {
  color: var(--c-warn);
}
.cx-summary {
  font-size: 13.5px;
  color: var(--c-ink-2);
  margin: 0;
  line-height: 1.7;
}
.next-questions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.next-q {
  text-align: left;
  background: var(--c-bg);
  border: 1px solid var(--c-line);
  border-radius: var(--radius);
  padding: 9px 14px;
  font-size: 13.5px;
  cursor: pointer;
  font-family: var(--font);
  color: var(--c-ink-2);
  transition: all 0.15s;
}
.next-q:hover {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.vd-footer {
  margin-top: 26px;
  border-top: 2px solid var(--c-judge-line);
  padding-top: 16px;
}
.vd-clerk {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--c-judge);
  margin-bottom: 12px;
}
.vd-status-row {
  display: flex;
  gap: 14px;
  align-items: center;
  font-size: 12.5px;
  color: var(--c-ink-3);
  margin-bottom: 10px;
}
.vd-disclaimer {
  font-size: 12px;
  color: var(--c-ink-3);
  line-height: 1.7;
  margin: 0;
}

@media (max-width: 800px) {
  .vd-two-col {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 640px) {
  .doc-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .doc-actions > a:first-child {
    grid-column: 1 / -1;
  }
  .doc-hint {
    grid-column: 1 / -1;
  }
  .doc-actions :deep(.n-button) {
    width: 100%;
  }
  .verdict-doc {
    padding: 26px 20px;
  }
  .vd-two-col {
    grid-template-columns: 1fr;
  }
  .vd-header h1 {
    font-size: 24px;
    letter-spacing: 3px;
  }
}
</style>
