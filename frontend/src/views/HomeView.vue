<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import DemoBadge from '@/components/common/DemoBadge.vue'
import CourtClerk from '@/components/common/CourtClerk.vue'
import type { HackathonContentItem, HackathonContentKind, HotItem } from '@/types'

const router = useRouter()
const message = useMessage()
const question = ref('')
const hot = ref<HotItem[]>([])
const hotDemo = ref(false)
const hotLoading = ref(true)
const hotError = ref(false)
const contentKind = ref<HackathonContentKind>('knowledge')
const curated = ref<HackathonContentItem[]>([])
const curatedLoading = ref(true)
const curatedError = ref(false)
const curatedRequestId = ref(0)
const submitting = ref(false)

const workflow = [
  { title: '明确命题', desc: 'AI 将模糊问题重写为可审理的明确命题' },
  { title: '取证', desc: '检索知乎真实讨论与全网公开资料' },
  { title: '质证', desc: 'Evidence Agent 提取结构化证据并排序' },
  { title: '对抗', desc: '控辩双方基于证据构建最强论证' },
  { title: '宣判', desc: 'Judge 基于证据结构给出条件性判断' },
]

const examples = [
  'AI 会不会淘汰程序员？',
  '年轻人应该提前还房贷吗？',
  '电动汽车真的更环保吗？',
  '读研的意义还大吗？',
]

async function loadHot() {
  try {
    const res = await api.hot()
    hot.value = res.items ?? []
    hotDemo.value = res.is_demo
    hotError.value = !!res.error || hot.value.length === 0
  } catch {
    hotError.value = true
  } finally {
    hotLoading.value = false
  }
}

async function loadCurated(kind: HackathonContentKind) {
  const requestId = ++curatedRequestId.value
  contentKind.value = kind
  curatedLoading.value = true
  curatedError.value = false
  try {
    const res = await api.hackathonContent(kind, 12)
    // 请求序号同时覆盖 knowledge → story → knowledge，较早的同类响应也不能覆盖最新选择。
    if (requestId !== curatedRequestId.value) return
    curated.value = res.items ?? []
    curatedError.value = !!res.error || curated.value.length === 0
  } catch {
    if (requestId === curatedRequestId.value) {
      curated.value = []
      curatedError.value = true
    }
  } finally {
    if (requestId === curatedRequestId.value) curatedLoading.value = false
  }
}

async function submit() {
  if (submitting.value) return
  const q = question.value.trim()
  if (q.length < 4) {
    message.warning('请输入至少 4 个字的问题')
    return
  }
  submitting.value = true
  try {
    // 先做命题规划（不创建案件），确认页在 /case/new
    router.push({ path: '/case/new', query: { q } })
  } finally {
    submitting.value = false
  }
}

function useExample(ex: string) {
  question.value = ex
}

function fromHot(item: HotItem) {
  router.push({ path: '/case/new', query: { q: item.title.replace(/^【演示数据】/, '') } })
}

function fromCurated(item: HackathonContentItem) {
  const title = item.title.replace(/[？?。！!]$/, '')
  const q = contentKind.value === 'knowledge'
    ? `${title}中的观点是否成立？`
    : `如何评价《${title}》中呈现的选择与观点？`
  router.push({ path: '/case/new', query: { q } })
}

onMounted(() => {
  void loadHot()
  void loadCurated('knowledge')
})
</script>

<template>
  <main class="page home">
    <!-- Hero -->
    <section class="hero">
      <div class="hero-badge">知乎黑客松 · 知识炼金场参赛作品</div>
      <h1>让观点接受<span class="hl">证据审理</span></h1>
      <p class="sub">
        AI 时代，答案不再稀缺——稀缺的是理解、判断与证据意识。
        ZhiCourt 把真实争议变成一场结构化的"知识审理"：
        Source → Evidence → Claim → Argument → Verdict，每一步都可追溯。
      </p>

      <div class="input-wrap">
        <div class="clerk-hero">
          <div class="clerk-bubble">有争议的问题？<br />我来协助记录</div>
          <CourtClerk :variant="question.trim().length > 0 ? 'greet' : 'idle'" :size="120" />
        </div>
        <div class="input-box card">
          <textarea
            v-model="question"
            class="text-input hero-input"
            rows="2"
            maxlength="500"
            placeholder="输入一个有争议的问题，例如：AI 会不会淘汰程序员？"
            @keydown.enter.exact.prevent="submit"
          />
          <div class="input-foot">
            <div class="examples">
              <span>试试：</span>
              <button v-for="ex in examples" :key="ex" class="example-chip" @click="useExample(ex)">{{ ex }}</button>
            </div>
            <NButton type="primary" size="large" :loading="submitting" @click="submit">发起审理 →</NButton>
          </div>
        </div>
      </div>
    </section>

    <!-- 工作流程 -->
    <section class="workflow">
      <h2 class="sec-title">知识如何被审理</h2>
      <div class="flow-row">
        <div v-for="(step, i) in workflow" :key="step.title" class="flow-step card">
          <div class="step-num">{{ i + 1 }}</div>
          <h3>{{ step.title }}</h3>
          <p>{{ step.desc }}</p>
        </div>
      </div>
    </section>

    <!-- 官方活动内容灵感库（无需 Access Secret） -->
    <section class="curated-section" aria-labelledby="curated-heading">
      <div class="curated-head">
        <div>
          <h2 id="curated-heading" class="sec-title">知乎黑客松内容灵感库</h2>
          <p>浏览知乎黑客松官方提供的知识与故事内容，从中发现值得审理的问题。所选内容仅作为选题线索，正式审理时会重新检索并核验证据。</p>
        </div>
        <div class="kind-tabs" role="tablist" aria-label="活动内容类型">
          <button
            v-for="kind in (['knowledge', 'story'] as const)"
            :key="kind"
            role="tab"
            :aria-selected="contentKind === kind"
            :class="{ active: contentKind === kind }"
            @click="loadCurated(kind)"
          >
            {{ kind === 'knowledge' ? '知乎知识' : '知乎故事' }}
          </button>
        </div>
      </div>
      <div v-if="curatedLoading" class="curated-grid" aria-busy="true">
        <div v-for="i in 4" :key="i" class="skeleton-block" style="height: 142px" />
      </div>
      <div v-else-if="curatedError" class="empty-state card">
        <div class="icon">⌇</div>
        <p>官方活动内容暂时不可用，您仍可以直接输入问题发起审理。</p>
      </div>
      <div v-else class="curated-grid">
        <button
          v-for="item in curated.slice(0, 8)"
          :key="item.work_id"
          class="curated-card card"
          :aria-label="`以“${item.title}”为线索发起审理`"
          @click="fromCurated(item)"
        >
          <div class="curated-labels">
            <span v-for="label in item.labels.slice(0, 3)" :key="label">{{ label }}</span>
          </div>
          <h3>{{ item.title }}</h3>
          <p>{{ item.description || '选择这个知乎活动内容，生成一个可审理的问题。' }}</p>
          <div class="curated-action">围绕此内容发起审理 →</div>
        </button>
      </div>
    </section>

    <!-- 热榜案件 -->
    <section class="hot-section">
      <div class="hot-head">
        <h2 class="sec-title" style="margin: 0">来自知乎热榜</h2>
        <DemoBadge :is-demo="hotDemo" />
      </div>
      <div v-if="hotLoading" class="hot-grid">
        <div v-for="i in 4" :key="i" class="skeleton-block" style="height: 92px" />
      </div>
      <div v-else-if="hotError" class="empty-state card">
        <div class="icon">⌇</div>
        <p>热榜暂时不可用，您仍可以直接输入问题发起审理。</p>
      </div>
      <div v-else class="hot-grid">
        <button v-for="(item, i) in hot.slice(0, 8)" :key="i" class="hot-card card" @click="fromHot(item)">
          <div class="hot-rank" :class="{ top: i < 3 }">{{ i + 1 }}</div>
          <div class="hot-body">
            <div class="hot-title">{{ item.title }}</div>
            <div class="hot-meta">{{ item.heat > 0 ? (item.heat / 10000).toFixed(0) + ' 万热度' : '知乎热榜' }}</div>
          </div>
        </button>
      </div>
    </section>

    <!-- 理念 -->
    <section class="philosophy card card-pad">
      <h2 class="sec-title" style="margin-top: 0">不是替你得到答案，而是让你看清答案为什么成立</h2>
      <div class="phil-grid">
        <div>
          <h4>可追溯</h4>
          <p>每个结论都能沿 Source → Evidence → Claim → Argument → Verdict 完整追溯，点击即达。</p>
        </div>
        <div>
          <h4>区分事实与观点</h4>
          <p>事实、观点、推断、预测、未知被明确标注，模型生成的内容不会被当作事实。</p>
        </div>
        <div>
          <h4>Steelman 而非吵架</h4>
          <p>控辩双方都基于真实证据构建最强论证，质证方专职检查逻辑漏洞。</p>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.home {
  max-width: 1080px;
}

/* Hero */
.hero {
  text-align: center;
  padding: 64px 0 44px;
}
.hero-badge {
  display: inline-block;
  font-size: 12px;
  color: var(--c-primary);
  background: var(--c-primary-bg);
  border: 1px solid #cfe1fb;
  border-radius: 20px;
  padding: 4px 14px;
  margin-bottom: 22px;
}
.hero h1 {
  font-size: 44px;
  letter-spacing: 1px;
}
.hero .hl {
  color: var(--c-primary);
  position: relative;
}
.hero .sub {
  max-width: 640px;
  margin: 18px auto 34px;
  color: var(--c-ink-3);
  font-size: 15.5px;
}
.input-wrap {
  position: relative;
  max-width: 720px;
  margin: 0 auto;
}
.clerk-hero {
  position: absolute;
  right: -108px;
  bottom: -10px;
  display: flex;
  align-items: flex-end;
  gap: 4px;
  z-index: 2;
  pointer-events: none;
}
.clerk-bubble {
  background: #fff;
  border: 1px solid var(--c-line);
  border-radius: 12px 12px 3px 12px;
  padding: 7px 11px;
  font-size: 12px;
  color: var(--c-ink-2);
  box-shadow: var(--shadow-card);
  white-space: nowrap;
  margin-bottom: 118px;
  position: relative;
}
.clerk-bubble::after {
  content: '';
  position: absolute;
  right: 14px;
  bottom: -5px;
  width: 8px;
  height: 8px;
  background: #fff;
  border-right: 1px solid var(--c-line);
  border-bottom: 1px solid var(--c-line);
  transform: rotate(45deg);
}
.input-box {
  padding: 18px 20px 14px;
  text-align: left;
}
.hero-input {
  border: none;
  padding: 4px 6px;
  font-size: 16px;
  box-shadow: none !important;
}
.input-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  border-top: 1px dashed var(--c-line);
  padding-top: 12px;
  flex-wrap: wrap;
}
.examples {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--c-ink-3);
  font-size: 12px;
  flex-wrap: wrap;
}
.example-chip {
  border: 1px solid var(--c-line);
  background: var(--c-bg);
  border-radius: 14px;
  font-size: 12px;
  padding: 2px 10px;
  cursor: pointer;
  color: var(--c-ink-2);
  font-family: var(--font);
}
.example-chip:hover {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

/* Workflow */
.workflow {
  margin: 30px 0;
}
.sec-title {
  text-align: center;
  font-size: 22px;
  margin-bottom: 22px;
}
.flow-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
}
.flow-step {
  padding: 18px 16px;
  position: relative;
}
.step-num {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: var(--c-primary-bg);
  color: var(--c-primary);
  font-weight: 700;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 10px;
}
.flow-step h3 {
  font-size: 15px;
  margin-bottom: 6px;
}
.flow-step p {
  font-size: 12.5px;
  color: var(--c-ink-3);
  margin: 0;
  line-height: 1.6;
}

/* Hot */
.curated-section {
  margin: 36px 0;
}
.curated-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}
.curated-head .sec-title {
  text-align: left;
  margin: 0 0 5px;
}
.curated-head p {
  margin: 0;
  color: var(--c-ink-3);
  font-size: 12.5px;
}
.kind-tabs {
  display: flex;
  padding: 3px;
  border: 1px solid var(--c-line);
  border-radius: 10px;
  background: var(--c-bg);
  flex-shrink: 0;
}
.kind-tabs button {
  border: 0;
  border-radius: 7px;
  padding: 6px 13px;
  color: var(--c-ink-3);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 12.5px;
}
.kind-tabs button.active {
  color: var(--c-primary);
  background: #fff;
  box-shadow: var(--shadow-card);
}
.curated-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.curated-card {
  min-width: 0;
  padding: 16px;
  text-align: left;
  font-family: var(--font);
  cursor: pointer;
  transition: box-shadow 0.15s, transform 0.15s;
}
.curated-card:hover {
  box-shadow: var(--shadow-float);
  transform: translateY(-2px);
}
.curated-card h3 {
  margin: 8px 0 6px;
  font-size: 14px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.curated-card p {
  margin: 0;
  min-height: 40px;
  color: var(--c-ink-3);
  font-size: 12px;
  line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.curated-labels {
  min-height: 20px;
  display: flex;
  gap: 5px;
  overflow: hidden;
}
.curated-labels span {
  max-width: 90px;
  padding: 2px 7px;
  border-radius: 10px;
  background: var(--c-primary-bg);
  color: var(--c-primary);
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.curated-action {
  margin-top: 10px;
  color: var(--c-primary);
  font-size: 12px;
}
.hot-section {
  margin: 36px 0;
}
.hot-head {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 18px;
}
.hot-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
.hot-card {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  text-align: left;
  font-family: var(--font);
  align-items: flex-start;
  transition: box-shadow 0.15s, transform 0.15s;
}
.hot-card:hover {
  box-shadow: var(--shadow-float);
  transform: translateY(-2px);
}
.hot-rank {
  font-family: var(--mono);
  font-weight: 700;
  font-size: 16px;
  color: var(--c-ink-3);
  width: 20px;
  flex-shrink: 0;
}
.hot-rank.top {
  color: var(--c-pro);
}
.hot-title {
  font-size: 13.5px;
  font-weight: 500;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.hot-meta {
  font-size: 12px;
  color: var(--c-ink-3);
  margin-top: 5px;
}

/* Philosophy */
.philosophy {
  margin-top: 36px;
  background: linear-gradient(180deg, #fbfcfe, #f4f6fa);
}
.philosophy .sec-title {
  text-align: left;
  font-size: 18px;
}
.phil-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 26px;
}
.phil-grid h4 {
  font-size: 14.5px;
  margin-bottom: 6px;
  color: var(--c-ink);
}
.phil-grid p {
  font-size: 13px;
  color: var(--c-ink-3);
  margin: 0;
  line-height: 1.7;
}
@media (max-width: 1024px) {
  .clerk-hero {
    display: none;
  }
}

@media (max-width: 900px) {
  .flow-row {
    grid-template-columns: repeat(2, 1fr);
  }
  .hot-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .curated-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .phil-grid {
    grid-template-columns: 1fr;
  }
  .hero h1 {
    font-size: 32px;
  }
}
@media (max-width: 560px) {
  .flow-row,
  .hot-grid,
  .curated-grid {
    grid-template-columns: 1fr;
  }
  .curated-head {
    align-items: stretch;
    flex-direction: column;
  }
  .kind-tabs {
    align-self: flex-start;
  }
  .input-foot {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
