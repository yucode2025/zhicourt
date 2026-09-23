<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import type { PlanInfo } from '@/types'
import { preferredScrollBehavior } from '@/utils/display'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const question = ref('')
const plan = ref<PlanInfo | null>(null)
const planLoading = ref(false)
const planError = ref('')
const editing = ref(false)
const editedProposition = ref('')
const planSection = ref<HTMLElement | null>(null)
const creating = ref(false)
let plannedQuestion = ''
let planRequest = 0

const modeLabel = computed(() => (plan.value?.mode === 'llm' ? 'LLM 分析' : '启发式分析（演示模式）'))

async function handleAccessError(error: unknown): Promise<boolean> {
  if (!(error instanceof ApiError)) return false
  const redirect = route.fullPath
  if (error.code === 'authentication_required') {
    await router.push({ path: '/login', query: { redirect, required: 'authenticated' } })
    return true
  }
  if (error.code === 'zhihu_oauth_required') {
    await router.push({ path: '/profile', query: { tab: 'settings', redirect } })
    return true
  }
  return false
}

async function generatePlan() {
  if (planLoading.value) return
  const q = question.value.trim()
  if (q.length < 4) {
    planError.value = '请输入至少 4 个字的问题'
    return
  }
  const request = ++planRequest
  planLoading.value = true
  planError.value = ''
  plan.value = null
  editing.value = false
  try {
    const nextPlan = await api.plan(q)
    if (request !== planRequest || question.value.trim() !== q) return
    plan.value = nextPlan
    plannedQuestion = q
    editedProposition.value = nextPlan.proposition
    if (!nextPlan.suitable) {
      planError.value = nextPlan.rejection_reason || '该问题不适合进入知识法庭。'
    } else {
      await nextTick()
      planSection.value?.scrollIntoView({
        behavior: preferredScrollBehavior(window.matchMedia('(prefers-reduced-motion: reduce)').matches),
        block: 'start',
      })
    }
  } catch (e) {
    if (request === planRequest && !(await handleAccessError(e))) {
      planError.value = e instanceof Error ? e.message : '规划失败'
    }
  } finally {
    if (request === planRequest) planLoading.value = false
  }
}

async function confirmAndStart() {
  if (creating.value || !plan.value || question.value.trim() !== plannedQuestion) return
  creating.value = true
  let createdId = ''
  try {
    const kase = await api.createCase(question.value.trim(), editedProposition.value.trim() || plan.value.proposition)
    createdId = kase.id
    await api.startCase(kase.id)
    message.success('案件已受理，正在开庭')
    await router.push(`/case/${kase.id}`)
  } catch (e) {
    if (createdId) {
      message.warning('案件已创建，但暂未成功开庭，可在案件页重试')
      await router.push(`/case/${createdId}`)
    } else if (!(await handleAccessError(e))) {
      message.error(e instanceof Error ? e.message : '创建失败')
    }
  } finally {
    creating.value = false
  }
}

watch(question, (value) => {
  if (plan.value && value.trim() !== plannedQuestion) {
    plan.value = null
    editedProposition.value = ''
    editing.value = false
    planError.value = '问题已修改，请重新生成审理命题。'
  }
})

watch(
  () => route.query.q,
  (query) => {
    const next = typeof query === 'string' ? query : ''
    if (next && next !== question.value) {
      question.value = next
      void generatePlan()
    }
  },
  { immediate: true },
)
</script>

<template>
  <main class="page page-narrow">
    <h1 class="page-title">创建案件</h1>
    <p class="page-sub">AI 会把你的问题重写为适合证据审理的明确命题，确认后正式开庭。</p>

    <!-- Step 1: 输入 -->
    <div class="card card-pad step-card">
      <div class="step-head"><span class="step-no">1</span> 你要审理的问题</div>
      <textarea
        v-model="question"
        class="text-input"
        rows="3"
        maxlength="500"
        placeholder="例如：AI 会不会淘汰程序员？"
      />
      <div class="step-actions">
        <span class="char-count">{{ question.length }}/500</span>
        <NButton type="primary" :loading="planLoading" :disabled="question.trim().length < 4" @click="generatePlan">
          {{ plan ? '重新分析' : '生成审理命题' }}
        </NButton>
      </div>
    </div>

    <div v-if="planError" class="error-state">
      <h3>无法进入审理</h3>
      <p>{{ planError }}</p>
      <NButton v-if="plan && !plan.suitable" @click="router.push('/')">换个问题</NButton>
      <NButton v-else @click="generatePlan">重试</NButton>
    </div>

    <!-- Step 2: 确认命题 -->
    <template v-if="plan && plan.suitable">
      <div ref="planSection" class="card card-pad step-card">
        <div class="step-head"><span class="step-no">2</span> 确认审理命题 <span class="mode-tag">{{ modeLabel }}</span></div>

        <div class="prop-block">
          <div class="prop-label">审理命题</div>
          <template v-if="!editing">
            <div class="prop-text">{{ editedProposition }}</div>
            <button class="link-btn" @click="editing = true">修改</button>
          </template>
          <template v-else>
            <input v-model="editedProposition" class="text-input" maxlength="300" />
            <div class="edit-hint">命题应是一句可辩论的明确判断，双方都需要能举出证据。</div>
            <div style="margin-top: 8px; text-align: right">
              <NButton size="small" @click="editing = false; editedProposition = plan.proposition">取消</NButton>
              <NButton size="small" type="primary" style="margin-left: 8px" @click="editing = false">确定</NButton>
            </div>
          </template>
        </div>

        <div v-if="plan.key_concepts.length" class="kv-grid">
          <div class="kv">
            <div class="kv-label">关键概念</div>
            <div class="kv-items">
              <span v-for="c in plan.key_concepts" :key="c" class="chip">{{ c }}</span>
            </div>
          </div>
          <div class="kv">
            <div class="kv-label">可能的核心争议</div>
            <ul class="kv-list">
              <li v-for="d in plan.disputes" :key="d">{{ d }}</li>
            </ul>
          </div>
          <div class="kv">
            <div class="kv-label">检索方向</div>
            <div class="kv-items">
              <span v-for="s in plan.search_queries" :key="s" class="chip dim">{{ s }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 3: 流程预告 -->
      <div class="card card-pad step-card">
        <div class="step-head"><span class="step-no">3</span> 开庭后会发生什么</div>
        <ol class="pipeline">
          <li>检索当前可用的知乎与全网来源并去重排序；演示来源会被明确标注且不参与现实裁决</li>
          <li>Evidence Agent 提取结构化证据（区分事实 / 观点 / 数据 / 预测）</li>
          <li>控辩双方分别基于证据构建最强论证（全程可追溯）</li>
          <li>Cross Examiner 检查逻辑漏洞，Judge 生成《知识判决书》</li>
        </ol>
        <div class="start-row">
          <NButton type="primary" size="large" :loading="creating" @click="confirmAndStart">
            开始审理 →
          </NButton>
        </div>
      </div>
    </template>

    <!-- 骨架 -->
    <div v-if="planLoading" class="card card-pad" style="margin-top: 20px">
      <div class="skeleton-block" style="height: 22px; width: 40%; margin-bottom: 14px" />
      <div class="skeleton-block" style="height: 54px; margin-bottom: 14px" />
      <div class="skeleton-block" style="height: 16px; width: 70%; margin-bottom: 8px" />
      <div class="skeleton-block" style="height: 16px; width: 60%" />
    </div>
  </main>
</template>

<style scoped>
.page-title {
  font-size: 26px;
}
.page-sub {
  color: var(--c-ink-3);
  margin: 8px 0 24px;
}
.step-card {
  margin-bottom: 18px;
}
.step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
  font-size: 16px;
  margin-bottom: 14px;
}
.step-no {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--c-primary-bg);
  color: var(--c-primary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.mode-tag {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--c-ink-3);
  border: 1px solid var(--c-line);
  border-radius: 12px;
  padding: 1px 10px;
}
.step-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.char-count {
  font-size: 12px;
  color: var(--c-ink-3);
}
.prop-block {
  background: var(--c-primary-bg);
  border: 1px solid #cfe1fb;
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 16px;
}
.prop-label {
  font-size: 12px;
  color: var(--c-primary);
  font-weight: 600;
  margin-bottom: 6px;
}
.prop-text {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.55;
}
.link-btn {
  background: none;
  border: none;
  color: var(--c-primary);
  cursor: pointer;
  font-size: 13px;
  margin-top: 8px;
  padding: 0;
  font-family: var(--font);
}
.edit-hint {
  font-size: 12px;
  color: var(--c-ink-3);
  margin-top: 8px;
}
.kv-grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.kv-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 7px;
  color: var(--c-ink-2);
}
.kv-items {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.chip {
  font-size: 12.5px;
  background: var(--c-bg);
  border: 1px solid var(--c-line);
  border-radius: 5px;
  padding: 2px 10px;
  color: var(--c-ink-2);
}
.chip.dim {
  color: var(--c-ink-3);
}
.kv-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13.5px;
  color: var(--c-ink-2);
}
.pipeline {
  margin: 0 0 18px;
  padding-left: 20px;
  color: var(--c-ink-2);
  font-size: 14px;
}
.pipeline li {
  margin-bottom: 7px;
}
.start-row {
  text-align: right;
}
</style>
