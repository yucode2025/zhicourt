<script setup lang="ts">
import { Background } from '@vue-flow/background'
import { Handle, Position, VueFlow, useVueFlow, type Edge, type Node } from '@vue-flow/core'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NDrawer, NDrawerContent } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { ArgumentItem, CaseDetail, ClaimItem, EvidenceItem, SourceItem } from '@/types'
import { evidenceTypeLabels } from '@/utils/display'
import '@vue-flow/core/dist/style.css'
import ScoreWidget from '@/components/common/ScoreWidget.vue'
import SourceCard from '@/components/source/SourceCard.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'
import { graphUnavailableReason, hasCoreEvidenceLinks, resolveArgumentClaims } from '@/utils/evidenceGraph'

const route = useRoute()
const caseId = computed(() => route.params.id as string)
const { fitView, zoomIn, zoomOut } = useVueFlow()

const kase = ref<CaseDetail | null>(null)
const loading = ref(true)
const error = ref('')
const drawer = ref(false)
const drawerSource = ref<SourceItem | null>(null)
const drawerEvidence = ref<EvidenceItem | null>(null)
const drawerClaim = ref<ClaimItem | null>(null)
const drawerArgument = ref<ArgumentItem | null>(null)
const drawerVerdict = ref(false)
const graphMode = ref<'core' | 'all'>('core')
const viewportW = ref(window.innerWidth)
const isNarrow = computed(() => viewportW.value <= 896)
const viewPreference = ref<'auto' | 'list' | 'canvas'>('auto')
const viewMode = computed<'list' | 'canvas'>(() =>
  viewPreference.value === 'auto' ? (isNarrow.value ? 'list' : 'canvas') : viewPreference.value,
)
let drawerTrigger: HTMLElement | null = null
let fitTimer: number | null = null

function onViewportChange() {
  viewportW.value = window.innerWidth
}

const graphState = computed(() => graphUnavailableReason(kase.value))
const coreEmpty = computed(() => !!kase.value && !graphState.value && !hasCoreEvidenceLinks(kase.value))

const graphStats = computed(() => {
  const k = kase.value
  if (!k) return ''
  return [
    `${k.sources.length} 来源`, `${k.evidence.length} 证据`, `${k.claims.length} 主张`, `${k.arguments.length} 论证`,
    k.verdict ? '1 判决' : '暂无判决',
  ].join(' · ')
})

const chainRows = computed(() => {
  const k = kase.value
  if (!k) return []
  return k.arguments.map((argument) => {
    const evidence = argument.evidence_ids
      .map((id) => k.evidence.find((item) => item.id === id))
      .filter((item): item is EvidenceItem => !!item)
    return {
      argument,
      evidence,
      sources: evidence.map((item) => k.sources.find((source) => source.id === item.source_id) ?? null),
      claims: resolveArgumentClaims(k, argument),
      missingEvidenceIds: argument.evidence_ids.filter((id) => !k.evidence.some((item) => item.id === id)),
    }
  })
})

const diagnostics = computed(() => {
  const k = kase.value
  if (!k) return []
  const issues: string[] = []
  const citedEvidence = new Set(k.arguments.flatMap((argument) => argument.evidence_ids))
  const linkedClaims = new Set(k.arguments.flatMap((argument) => resolveArgumentClaims(k, argument).items.map((claim) => claim.id)))
  for (const source of k.sources) {
    if (!k.evidence.some((evidence) => evidence.source_id === source.id)) issues.push(`孤立来源：${source.title}`)
  }
  for (const evidence of k.evidence) {
    if (!k.sources.some((source) => source.id === evidence.source_id)) issues.push(`证据缺少来源：${evidence.claim}`)
    if (!citedEvidence.has(evidence.id)) issues.push(`孤立证据：${evidence.claim}`)
  }
  for (const claim of k.claims) {
    const missing = claim.evidence_ids.filter((id) => !k.evidence.some((evidence) => evidence.id === id))
    if (missing.length) issues.push(`主张「${claim.text}」引用缺失证据：${missing.join('、')}`)
    if (!linkedClaims.has(claim.id)) issues.push(`孤立主张：${claim.text}`)
  }
  for (const row of chainRows.value) {
    if (!row.argument.evidence_ids.length) issues.push(`论证「${row.argument.title}」没有引用证据`)
    if (row.missingEvidenceIds.length) issues.push(`论证「${row.argument.title}」引用缺失证据：${row.missingEvidenceIds.join('、')}`)
    if (row.claims.missingIds.length) issues.push(`论证「${row.argument.title}」引用缺失主张：${row.claims.missingIds.join('、')}`)
  }
  return issues
})

function nodeStyle(layer: string, side: string): Record<string, string> {
  const palette =
    side === 'pro' || side === 'prosecution'
      ? { color: '#a53f46', bg: '#fff8f8', border: '#be5c62' }
      : side === 'con' || side === 'defense'
        ? { color: '#355f91', bg: '#f7faff', border: '#4f79ab' }
        : side === 'court'
          ? { color: '#766331', bg: '#faf7ee', border: '#9a8548' }
          : side === 'zhihu'
            ? { color: '#056de8', bg: '#f7faff', border: '#77a9e8' }
            : { color: '#397a75', bg: '#f6fbfa', border: '#72aaa6' }
  return {
    width: layer === 'claim' ? '200px' : layer === 'verdict' ? '170px' : '184px',
    minHeight: layer === 'evidence' ? '86px' : '76px',
    background: palette.bg,
    border: `1.5px solid ${palette.border}`,
    borderRadius: layer === 'verdict' ? '12px' : '8px',
    color: palette.color,
    boxShadow: '0 2px 6px rgba(26,26,31,.06)',
  }
}

function edgeStyle(side: string, inferred = false) {
  return {
    stroke: side === 'pro' || side === 'prosecution' ? '#c9797d' : side === 'con' || side === 'defense' ? '#7193b8' : '#9aa0aa',
    strokeWidth: 1.5,
    ...(inferred ? { strokeDasharray: '6 5' } : {}),
  }
}

const graphData = computed(() => {
  const k = kase.value
  const nodes: Node[] = []
  const edges: Edge[] = []
  if (!k) return { nodes, edges }

  const referencedEvidence = new Set(k.arguments.flatMap((argument) => argument.evidence_ids))
  const linkedClaimIds = new Set(k.arguments.flatMap((argument) => resolveArgumentClaims(k, argument).items.map((claim) => claim.id)))
  const includedEvidence = graphMode.value === 'all' ? k.evidence : k.evidence.filter((item) => referencedEvidence.has(item.id))
  const includedClaims = graphMode.value === 'all' ? k.claims : k.claims.filter((item) => linkedClaimIds.has(item.id))
  const includedSourceIds = new Set(includedEvidence.map((item) => item.source_id))
  const includedSources = graphMode.value === 'all' ? k.sources : k.sources.filter((item) => includedSourceIds.has(item.id))
  const layers = [includedSources, includedEvidence, includedClaims, k.arguments]
  const x = [30, 300, 580, 870]
  const canvasHeight = Math.max(620, ...layers.map((items) => 100 + Math.max(0, items.length - 1) * 112))
  const yFor = (index: number, count: number) => count <= 1 ? canvasHeight / 2 - 40 : 70 + index * ((canvasHeight - 150) / (count - 1))

  includedSources.forEach((source, index) => nodes.push({
    id: `src:${source.id}`, position: { x: x[0], y: yFor(index, includedSources.length) },
    data: { label: source.title, source, layer: 'source' }, style: nodeStyle('source', source.origin),
  }))
  includedEvidence.forEach((evidence, index) => {
    nodes.push({ id: `ev:${evidence.id}`, position: { x: x[1], y: yFor(index, includedEvidence.length) }, data: { label: evidence.claim, evidence, layer: 'evidence' }, style: nodeStyle('evidence', evidence.stance) })
    if (includedSources.some((source) => source.id === evidence.source_id)) {
      edges.push({ id: `src-ev:${evidence.id}`, source: `src:${evidence.source_id}`, target: `ev:${evidence.id}`, type: 'smoothstep', style: edgeStyle(evidence.stance) })
    }
  })
  includedClaims.forEach((claim, index) => {
    nodes.push({ id: `clm:${claim.id}`, position: { x: x[2], y: yFor(index, includedClaims.length) }, data: { label: claim.text, claim, layer: 'claim' }, style: nodeStyle('claim', claim.side) })
    claim.evidence_ids.forEach((evidenceId) => {
      if (includedEvidence.some((evidence) => evidence.id === evidenceId)) edges.push({ id: `ev-clm:${evidenceId}:${claim.id}`, source: `ev:${evidenceId}`, target: `clm:${claim.id}`, type: 'smoothstep', style: edgeStyle(claim.side) })
    })
  })
  k.arguments.forEach((argument, index) => {
    nodes.push({ id: `arg:${argument.id}`, position: { x: x[3], y: yFor(index, k.arguments.length) }, data: { label: argument.title, argument, layer: 'argument' }, style: nodeStyle('argument', argument.side) })
    argument.evidence_ids.forEach((evidenceId) => {
      if (includedEvidence.some((evidence) => evidence.id === evidenceId)) edges.push({ id: `ev-arg:${evidenceId}:${argument.id}`, source: `ev:${evidenceId}`, target: `arg:${argument.id}`, type: 'smoothstep', style: edgeStyle(argument.side) })
    })
    const claims = resolveArgumentClaims(k, argument)
    claims.items.forEach((claim) => {
      if (includedClaims.some((item) => item.id === claim.id)) edges.push({
        id: `clm-arg:${claim.id}:${argument.id}`, source: `clm:${claim.id}`, target: `arg:${argument.id}`, type: 'smoothstep',
        label: claims.kind === 'inferred' ? '推断' : undefined, style: edgeStyle(argument.side, claims.kind === 'inferred'),
      })
    })
  })

  if (k.verdict) {
    nodes.push({ id: 'verdict', position: { x: 1160, y: canvasHeight / 2 - 40 }, data: { label: '知识判决书', layer: 'verdict' }, style: nodeStyle('verdict', 'court') })
    const verdictEvidence = new Set([...k.verdict.strongest_evidence_ids, ...k.verdict.strongest_counter_evidence_ids])
    verdictEvidence.forEach((evidenceId) => {
      if (includedEvidence.some((evidence) => evidence.id === evidenceId)) edges.push({ id: `ev-verdict:${evidenceId}`, source: `ev:${evidenceId}`, target: 'verdict', type: 'smoothstep', style: edgeStyle('court') })
    })
  }
  return { nodes, edges }
})

const nodes = computed(() => graphData.value.nodes)
const edges = computed(() => graphData.value.edges)

function clearDrawer() {
  drawerSource.value = null
  drawerEvidence.value = null
  drawerClaim.value = null
  drawerArgument.value = null
  drawerVerdict.value = false
}

function rememberTrigger(event?: Event) {
  drawerTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : document.activeElement instanceof HTMLElement ? document.activeElement : null
}

function openNodeData(data: { source?: SourceItem; evidence?: EvidenceItem; claim?: ClaimItem; argument?: ArgumentItem; layer?: string }, event?: Event) {
  const k = kase.value
  if (!k) return
  rememberTrigger(event)
  clearDrawer()
  if (data.evidence) {
    drawerEvidence.value = data.evidence
    drawerSource.value = data.source ?? k.sources.find((source) => source.id === data.evidence?.source_id) ?? null
  } else if (data.claim) drawerClaim.value = data.claim
  else if (data.argument) drawerArgument.value = data.argument
  else if (data.source) drawerSource.value = data.source
  else if (data.layer === 'verdict' && k.verdict) drawerVerdict.value = true
  drawer.value = !!(drawerEvidence.value || drawerClaim.value || drawerArgument.value || drawerSource.value || drawerVerdict.value)
}

function afterDrawerLeave() {
  clearDrawer()
  const target = drawerTrigger
  drawerTrigger = null
  if (target?.isConnected) target.focus()
}

function evidenceLabel(id: string) {
  return kase.value?.evidence.find((evidence) => evidence.id === id)?.claim ?? `缺失证据（${id}）`
}

function scheduleFitView() {
  if (fitTimer !== null) clearTimeout(fitTimer)
  fitTimer = window.setTimeout(async () => {
    fitTimer = null
    await nextTick()
    await fitView({ padding: graphMode.value === 'core' ? 0.14 : 0.08, duration: 300 })
  }, 50)
}

watch([viewMode, graphMode, nodes], ([mode]) => {
  if (mode === 'canvas') scheduleFitView()
}, { flush: 'post' })

watch(caseId, async (id) => {
  drawer.value = false
  kase.value = null
  await loadGraph(id)
})

async function loadGraph(id: string) {
  loading.value = true
  error.value = ''
  try {
    kase.value = await api.getCase(id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  window.addEventListener('resize', onViewportChange)
  void loadGraph(caseId.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onViewportChange)
  if (fitTimer !== null) clearTimeout(fitTimer)
})
</script>

<template>
  <main class="page graph-page">
    <div class="graph-head">
      <div>
        <h1 class="page-title">证据图谱</h1>
        <p v-if="kase" class="graph-sub">{{ kase.proposition }}</p>
        <p v-if="kase" class="graph-count">完整链路：{{ graphStats }}</p>
      </div>
      <div class="head-actions">
        <div v-if="kase && !graphState" class="mode-switch" role="group" aria-label="响应视图">
          <NButton size="small" :type="viewPreference === 'auto' ? 'primary' : 'default'" @click="viewPreference = 'auto'">自动（{{ isNarrow ? '列表' : '画布' }}）</NButton>
          <NButton size="small" :type="viewPreference === 'list' ? 'primary' : 'default'" @click="viewPreference = 'list'">手动列表</NButton>
          <NButton size="small" :type="viewPreference === 'canvas' ? 'primary' : 'default'" @click="viewPreference = 'canvas'">手动画布</NButton>
        </div>
        <template v-if="kase && !graphState && viewMode === 'canvas'">
          <div class="mode-switch" role="group" aria-label="画布范围">
            <NButton size="small" :type="graphMode === 'core' ? 'primary' : 'default'" @click="graphMode = 'core'">已引用链路</NButton>
            <NButton size="small" :type="graphMode === 'all' ? 'primary' : 'default'" @click="graphMode = 'all'">全部与孤立项</NButton>
          </div>
          <NButton size="small" aria-label="缩小画布" @click="zoomOut()">−</NButton>
          <NButton size="small" @click="scheduleFitView">适配画布</NButton>
          <NButton size="small" aria-label="放大画布" @click="zoomIn()">＋</NButton>
        </template>
        <AppLinkButton v-if="kase" :to="`/case/${kase.id}`" size="small">← 返回庭审现场</AppLinkButton>
      </div>
    </div>

    <div v-if="loading" class="skeleton-block" style="height: 640px" />
    <div v-else-if="error" class="error-state card"><h3>加载失败</h3><p>{{ error }}</p><NButton @click="loadGraph(caseId)">重试</NButton></div>

    <div v-else-if="kase && graphState" class="error-state card" role="status"><h2>图谱暂不可用</h2><p>{{ graphState }}</p><AppLinkButton :to="`/case/${kase.id}`">返回庭审现场</AppLinkButton></div>
    <div v-else-if="kase && viewMode === 'list'" class="chain-list">
      <div class="relation-notice card">
        实线关系来自后端明确 ID 引用。旧案件若没有 <code>claim_ids</code>，仅按共同 evidence_ids 展示虚线式“推断关系”，不按 stance 猜测或补造关系。
      </div>
      <div v-if="coreEmpty" class="error-state card">暂无已引用的核心链路；以下孤立证据和来源仍可在“全部与孤立项”画布中查看。</div>
      <div v-if="!chainRows.length" class="error-state card">暂无论证链路；{{ kase.verdict ? '判决已存在但没有论证引用。' : '案件尚未形成论证或判决。' }}</div>
      <article v-for="row in chainRows" :key="row.argument.id" class="chain-row card">
        <div class="chain-title"><span class="side-chip" :class="row.argument.side">{{ row.argument.side === 'prosecution' ? '控方' : row.argument.side === 'defense' ? '辩方' : '法庭' }}</span><b>{{ row.argument.title }}</b></div>
        <section class="chain-section">
          <h2>全部实际证据引用（{{ row.argument.evidence_ids.length }}）</h2>
          <div v-for="(evidence, index) in row.evidence" :key="evidence.id" class="citation-chain">
            <button v-if="row.sources[index]" type="button" class="chain-item layer source" @click="openNodeData({ source: row.sources[index]! }, $event)"><i>来源</i>{{ row.sources[index]!.title }}</button>
            <div v-else class="chain-gap">来源缺失：{{ evidence.source_id }}</div>
            <button type="button" class="chain-item layer evidence" @click="openNodeData({ evidence, source: row.sources[index] ?? undefined }, $event)"><i>证据</i>{{ evidence.claim }}</button>
          </div>
          <div v-for="id in row.missingEvidenceIds" :key="id" class="chain-gap">引用证据缺失：{{ id }}</div>
          <div v-if="!row.argument.evidence_ids.length" class="chain-gap">Gap：该论证没有 evidence_ids</div>
        </section>
        <section class="chain-section">
          <h2>主张关系 <span v-if="row.claims.kind === 'inferred'" class="inferred-tag">推断关系</span></h2>
          <button v-for="claim in row.claims.items" :key="claim.id" type="button" class="chain-item layer claim" @click="openNodeData({ claim }, $event)"><i>主张</i>{{ claim.text }}</button>
          <div v-if="!row.claims.items.length" class="chain-gap">没有可验证的 Claim 关系</div>
        </section>
        <button type="button" class="chain-item layer argument" @click="openNodeData({ argument: row.argument }, $event)"><i>论证</i>{{ row.argument.body }}</button>
      </article>
      <button v-if="kase.verdict" type="button" class="chain-item layer verdict chain-verdict" @click="openNodeData({ layer: 'verdict' }, $event)"><i>判决</i>{{ kase.verdict.conclusion }}</button>
      <div v-else class="relation-notice card">暂无判决：不会显示或连接虚构的 Verdict 节点。</div>
      <section v-if="diagnostics.length" class="diagnostics card" aria-labelledby="diagnostic-title">
        <h2 id="diagnostic-title">链路缺口与孤立项（{{ diagnostics.length }}）</h2>
        <ul><li v-for="issue in diagnostics" :key="issue">{{ issue }}</li></ul>
      </section>
    </div>

    <div v-else-if="kase" class="graph-wrap card">
      <div v-if="coreEmpty && graphMode === 'core'" class="graph-empty" role="status">暂无已引用的核心链路。切换到“全部与孤立项”查看未引用的证据和来源。</div>
      <div class="layer-guide"><span class="layer source">Source</span><i>→</i><span class="layer evidence">Evidence</span><i>→</i><span class="layer claim">Claim</span><i>→</i><span class="layer argument">Argument</span><i>→</i><span class="layer verdict">Verdict（存在时）</span><em>虚线“推断”仅兼容缺少 claim_ids 的旧数据</em></div>
      <VueFlow :nodes="nodes" :edges="edges" :default-viewport="{ x: 20, y: 30, zoom: 0.72 }" :min-zoom="0.25" :max-zoom="1.8" :nodes-draggable="false" :nodes-connectable="false" fit-view-on-init>
        <template #node-default="{ data }">
          <Handle v-if="data.layer !== 'source'" type="target" :position="Position.Left" />
          <button type="button" class="graph-node-btn" :aria-label="`查看${data.layer === 'verdict' ? '判决' : '节点'}详情：${String(data.label).slice(0, 40)}`" @click="openNodeData(data, $event)">{{ data.label }}</button>
          <Handle v-if="data.layer !== 'verdict'" type="source" :position="Position.Right" />
        </template>
        <Background pattern-color="#d4d7de" :gap="20" />
      </VueFlow>
    </div>

    <NDrawer v-model:show="drawer" :width="Math.min(460, viewportW)" placement="right" :mask-closable="true" :close-on-esc="true" @after-leave="afterDrawerLeave">
      <NDrawerContent closable :title="drawerEvidence ? '证据详情' : drawerClaim ? '主张详情' : drawerArgument ? '论证详情' : drawerVerdict ? '判决详情' : '来源详情'">
        <template v-if="drawerEvidence">
          <ScoreWidget :value="drawerEvidence.strength" />
          <div class="dt-block"><div class="dt-label">类型 / 立场</div><div>{{ evidenceTypeLabels[drawerEvidence.evidence_type] }} · {{ drawerEvidence.stance }}</div></div>
          <div class="dt-block"><div class="dt-label">证据主张</div><div>{{ drawerEvidence.claim }}</div></div>
          <div v-if="drawerEvidence.summary" class="dt-block"><div class="dt-label">完整摘要</div><div>{{ drawerEvidence.summary }}</div></div>
          <div v-if="drawerEvidence.limitations.length" class="dt-block"><div class="dt-label">提取与局限说明</div><ul><li v-for="item in drawerEvidence.limitations" :key="item">{{ item }}</li></ul></div>
          <SourceCard v-if="drawerSource" :source="drawerSource" />
          <p v-else class="chain-gap">该证据的来源不存在。</p>
        </template>
        <template v-else-if="drawerClaim">
          <div class="dt-block"><div class="dt-label">主张阵营</div><span class="side-chip" :class="drawerClaim.side === 'pro' ? 'prosecution' : drawerClaim.side === 'con' ? 'defense' : 'court'">{{ drawerClaim.side }}</span></div>
          <div class="dt-block"><div class="dt-label">主张内容</div><div>{{ drawerClaim.text }}</div></div>
          <div class="dt-block"><div class="dt-label">全部支撑证据</div><ul><li v-for="id in drawerClaim.evidence_ids" :key="id">{{ evidenceLabel(id) }}</li></ul></div>
        </template>
        <template v-else-if="drawerArgument">
          <ScoreWidget :value="drawerArgument.strength" label="论证强度" />
          <div class="dt-block"><div class="dt-label">论证标题</div><div>{{ drawerArgument.title }}</div></div>
          <div class="dt-block"><div class="dt-label">完整论证</div><div>{{ drawerArgument.body }}</div></div>
          <div class="dt-block"><div class="dt-label">全部实际引用证据</div><ul><li v-for="id in drawerArgument.evidence_ids" :key="id">{{ evidenceLabel(id) }}</li></ul></div>
        </template>
        <template v-else-if="drawerVerdict && kase?.verdict">
          <ScoreWidget :value="kase.verdict.confidence" label="判决置信度" warm />
          <div class="dt-block"><div class="dt-label">当前较合理结论</div><div>{{ kase.verdict.conclusion }}</div></div>
          <AppLinkButton :to="`/case/${kase.id}/verdict`" type="primary">打开完整《知识判决书》</AppLinkButton>
        </template>
        <template v-else-if="drawerSource"><SourceCard :source="drawerSource" /></template>
      </NDrawerContent>
    </NDrawer>
  </main>
</template>

<style scoped>
.graph-page { max-width: 1680px; }
.graph-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
.page-title { font-size: 24px; }
.graph-sub { color: var(--c-ink-2); font-size: 13.5px; margin: 4px 0 0; max-width: 900px; }
.graph-count { color: var(--c-primary); font-size: 12px; margin: 3px 0 0; }
.head-actions { display: flex; gap: 7px; flex-wrap: wrap; justify-content: flex-end; }
.mode-switch { display: inline-flex; border-right: 1px solid var(--c-line); padding-right: 8px; gap: 4px; flex-wrap: wrap; }
.graph-wrap { height: calc(100vh - 230px); min-height: 640px; overflow: hidden; position: relative; }
.graph-empty { position: absolute; z-index: 11; top: 65px; left: 14px; right: 14px; padding: 12px; background: #fff; border: 1px solid var(--c-line); border-radius: 8px; color: var(--c-ink-2); }
.layer-guide { position: absolute; top: 12px; left: 14px; right: 14px; z-index: 10; display: flex; gap: 8px; align-items: center; background: rgba(255,255,255,.94); border: 1px solid var(--c-line); border-radius: 8px; padding: 7px 10px; flex-wrap: wrap; box-shadow: var(--shadow-card); pointer-events: none; }
.layer { font-size: 11.5px; font-weight: 600; border-radius: 4px; }
.layer.source { color: #397a75; background: #edf7f5; }
.layer.evidence { color: #355f91; background: #f0f5fb; }
.layer.claim { color: #6a56b8; background: #f1eefa; }
.layer.argument { color: #a0474d; background: #faeeee; }
.layer.verdict { color: #766331; background: #f7f2e4; }
.layer-guide span { padding: 2px 7px; }
.layer-guide i { color: var(--c-ink-3); font-style: normal; }
.layer-guide em { margin-left: auto; font-size: 11px; color: var(--c-ink-3); font-style: normal; }
.dt-block { margin-bottom: 14px; font-size: 13.5px; line-height: 1.75; }
.dt-label { font-size: 12px; font-weight: 600; color: var(--c-ink-3); margin-bottom: 5px; }
.dt-block ul { margin: 4px 0 0; padding-left: 18px; }
:deep(.vue-flow__node) { text-align: left; overflow: visible; padding: 0 !important; }
:deep(.vue-flow__handle-left) { left: -5px; }
:deep(.vue-flow__handle-right) { right: -5px; }
.graph-node-btn { width: 100%; min-height: inherit; padding: 10px 12px; border: 0; border-radius: inherit; background: transparent; color: inherit; font: inherit; font-size: 12px; font-weight: 600; line-height: 1.45; text-align: left; cursor: pointer; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.graph-node-btn:focus-visible { outline: 3px solid var(--c-primary); outline-offset: 2px; }
.chain-list { display: flex; flex-direction: column; gap: 12px; }
.relation-notice, .diagnostics { padding: 12px 16px; color: var(--c-ink-2); font-size: 12.5px; }
.relation-notice code { font-family: var(--mono); }
.chain-row { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.chain-title { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.chain-section { display: flex; flex-direction: column; gap: 7px; }
.chain-section h2, .diagnostics h2 { font-size: 12.5px; color: var(--c-ink-2); }
.citation-chain { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 7px; }
.chain-item { display: block; width: 100%; border: 1px solid var(--c-line); border-radius: 8px; padding: 9px 12px; font-size: 13px; line-height: 1.6; cursor: pointer; overflow-wrap: anywhere; font-family: var(--font); text-align: left; color: var(--c-ink-2); background: #fff; }
.chain-item i { font-style: normal; font-size: 11px; font-weight: 700; margin-right: 8px; }
.chain-item.source { border-left: 3px solid #72aaa6; }
.chain-item.evidence { border-left: 3px solid #77a9e8; }
.chain-item.claim { border-left: 3px solid #8f7ad0; }
.chain-item.argument { border-left: 3px solid #c9797d; }
.chain-item.verdict { border-left: 3px solid #9a8548; }
.chain-gap { padding: 7px 10px; border: 1px dashed #d9a4a4; border-radius: 6px; color: var(--c-danger); font-size: 12px; background: var(--c-pro-bg); }
.inferred-tag { display: inline-block; margin-left: 6px; padding: 1px 6px; border: 1px dashed var(--c-warn); border-radius: 4px; color: var(--c-warn); font-size: 10.5px; }
.chain-verdict { width: 100%; }
.diagnostics ul { margin: 8px 0 0; padding-left: 20px; color: var(--c-danger); }
@media (max-width: 760px) {
  .graph-head { flex-direction: column; }
  .head-actions { justify-content: flex-start; }
  .graph-wrap { min-height: 560px; height: calc(100vh - 300px); }
  .layer-guide em { width: 100%; margin-left: 0; }
  .citation-chain { grid-template-columns: 1fr; }
}
</style>
