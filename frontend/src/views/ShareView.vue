<script setup lang="ts">
// 公开分享页：只读判决书展示（无质询/无操作按钮）
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import type { CaseDetail, VerdictItem } from '@/types'
import { conclusionStanceLabels, fmtTime } from '@/utils/display'
import DemoBadge from '@/components/common/DemoBadge.vue'
import ScoreWidget from '@/components/common/ScoreWidget.vue'
import ArgumentPanel from '@/components/court/ArgumentPanel.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'
import { shareOrCopy } from '@/utils/clipboard'

const route = useRoute()
const message = useMessage()
const publicId = computed(() => route.params.publicId as string)

const data = ref<{
  title: string; proposition: string; status: string; is_demo: boolean; engine_mode: string;
  source_mode: CaseDetail['source_mode']; execution_summary: CaseDetail['execution_summary'];
  created_at: string | null;
  sources: CaseDetail['sources']; evidence: CaseDetail['evidence'];
  arguments: CaseDetail['arguments']; cross_examinations: CaseDetail['cross_examinations'];
  verdict: VerdictItem | null;
} | null>(null)
const loading = ref(true)
const error = ref('')
const shareBusy = ref(false)
const shareError = ref('')
const manualShareUrl = ref('')

const proArgs = computed(() => data.value?.arguments.filter((a) => a.side === 'prosecution') ?? [])
const defArgs = computed(() => data.value?.arguments.filter((a) => a.side === 'defense') ?? [])
const proEv = computed(() => data.value?.evidence.filter((e) => e.stance === 'pro') ?? [])
const defEv = computed(() => data.value?.evidence.filter((e) => e.stance === 'con') ?? [])
const shareUrl = computed(() => window.location.href)
const sourceDisclaimer = computed(() => {
  if (data.value?.source_mode === 'mock') {
    return '当前只使用明确标注的演示来源，用于展示审理结构，不构成现实事实或倾向性判断。'
  }
  if (data.value?.source_mode === 'mixed') {
    return '本案包含真实与演示来源；演示来源不参与现实裁决，事实性结论应逐条核验。'
  }
  return '本案基于可追溯公开来源的结构化证据生成，置信度反映证据强度而非客观真理。'
})

async function copyLink() {
  if (shareBusy.value) return
  shareBusy.value = true
  shareError.value = ''
  manualShareUrl.value = ''
  try {
    const result = await shareOrCopy(shareUrl.value, data.value?.title ?? 'ZhiCourt 知识判决书')
    if (result === 'share') message.success('已打开系统分享')
    else if (result === 'clipboard') message.success('分享链接已复制')
    else {
      manualShareUrl.value = shareUrl.value
      shareError.value = '自动复制不可用，请从下方文本框手动复制。'
    }
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) {
      manualShareUrl.value = shareUrl.value
      shareError.value = '分享失败，请从下方文本框手动复制。'
    }
  } finally {
    shareBusy.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  data.value = null
  try {
    data.value = await api.getShared(publicId.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '分享内容不存在或已被设为私有'
  } finally {
    loading.value = false
  }
}

watch(publicId, load, { immediate: true })
</script>

<template>
  <main class="page page-narrow">
    <div v-if="loading" class="skeleton-page">
      <div class="skeleton-block" style="height: 44px; width: 50%; margin: 0 auto 16px" />
      <div class="skeleton-block" style="height: 420px" />
    </div>

    <div v-else-if="error" class="error-state card" style="margin-top: 40px">
      <h3>无法打开分享</h3>
      <p>{{ error }}</p>
      <AppLinkButton to="/" type="primary">访问 ZhiCourt 首页</AppLinkButton>
    </div>

    <template v-else-if="data">
      <div class="share-bar">
        <RouterLink to="/">← ZhiCourt · 知识法庭</RouterLink>
        <NButton size="small" :loading="shareBusy" :disabled="shareBusy" @click="copyLink">分享链接</NButton>
      </div>
      <p v-if="shareError" class="share-error" role="alert">{{ shareError }}</p>
      <label v-if="manualShareUrl" class="manual-share">
        <span>可复制分享链接</span>
        <input class="text-input" :value="manualShareUrl" readonly @focus="($event.target as HTMLInputElement).select()" />
      </label>

      <article class="share-doc card" data-testid="verdict-doc">
        <header style="text-align: center; margin-bottom: 22px">
          <div style="font-size: 12px; letter-spacing: 2px; color: var(--c-ink-3); margin-bottom: 8px">知识判决书 · 分享版</div>
          <h1 style="font-size: 26px" :title="data.title">{{ data.title }}</h1>
          <div style="margin-top: 10px; display: flex; gap: 12px; justify-content: center; align-items: center; font-size: 12px; color: var(--c-ink-3)">
            <DemoBadge :is-demo="data.is_demo" />
            <span>{{ fmtTime(data.created_at ?? '') }}</span>
            <span>{{ data.evidence.length }} 条证据 · {{ data.sources.length }} 个来源</span>
          </div>
        </header>

        <div class="prop-box">
          <span class="prop-label">审理命题</span>
          <div class="prop-text">{{ data.proposition }}</div>
        </div>

        <div v-if="data.verdict" class="verdict-box">
          <div class="vb-label">当前较合理结论</div>
          <div class="vb-text">
            <span class="vd-stance">{{ conclusionStanceLabels[data.verdict.conclusion_stance] }}</span>
            {{ data.verdict.conclusion }}
          </div>
          <ScoreWidget :value="data.verdict.confidence" label="判决置信度（非绝对真理）" warm />
        </div>

        <div v-if="!data.verdict" class="prop-box" style="border-style: solid; border-color: var(--c-line); background: var(--c-bg)">
          <span class="prop-label">暂无判决</span>
          <div class="prop-text" style="font-size: 13.5px; font-weight: 400; color: var(--c-ink-2)">
            该案件尚未生成判决书，当前仅展示庭审论证与证据。
          </div>
        </div>

        <div class="two-col">
          <ArgumentPanel
            side="prosecution"
            title="控方"
            subtitle="支持命题的论证"
            :arguments-list="proArgs"
            :evidence="proEv"
            :evidence-index="data.evidence"
            :inspectable="false"
            readonly
          />
          <ArgumentPanel
            side="defense"
            title="辩方"
            subtitle="反对命题的论证"
            :arguments-list="defArgs"
            :evidence="defEv"
            :evidence-index="data.evidence"
            :inspectable="false"
            readonly
          />
        </div>

        <div v-if="data.verdict" class="lists">
          <div class="list-block">
            <h4>双方共同认可</h4>
            <ul><li v-for="f in data.verdict.shared_facts" :key="f">{{ f }}</li></ul>
          </div>
          <div class="list-block">
            <h4>核心分歧</h4>
            <ul><li v-for="f in data.verdict.core_disputes" :key="f">{{ f }}</li></ul>
          </div>
        </div>

        <footer class="share-foot">
          <div class="share-clerk">
            <span>本判决书由系统生成并留存审理记录</span>
          </div>
          <p>
            {{ sourceDisclaimer }}
            引用关系可沿 Source → Evidence → Claim → Argument → Verdict 追溯。
          </p>
          <RouterLink to="/case/new" class="cta">在 ZhiCourt 发起你自己的审理 →</RouterLink>
        </footer>
      </article>
    </template>
  </main>
</template>

<style scoped>
.share-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
  font-size: 13.5px;
}
.share-error { color: var(--c-danger); font-size: 13px; margin: -6px 0 10px; }
.manual-share { display: block; color: var(--c-ink-3); font-size: 12px; margin-bottom: 12px; }
.manual-share .text-input { margin-top: 5px; padding: 8px 10px; font-size: 12px; }
.share-doc {
  padding: 36px 42px;
  border-top: 5px solid var(--c-judge);
}
.prop-box {
  border: 1px dashed var(--c-judge-line);
  border-radius: var(--radius);
  padding: 13px 18px;
  margin-bottom: 18px;
}
.prop-label {
  font-size: 12px;
  color: var(--c-judge);
  font-weight: 600;
}
.prop-text {
  font-size: 16px;
  font-weight: 600;
  margin-top: 4px;
}
.verdict-box {
  background: var(--c-judge-bg);
  border: 1px solid var(--c-judge-line);
  border-radius: var(--radius-lg);
  padding: 18px 22px;
  margin-bottom: 18px;
}
.vb-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-judge);
  margin-bottom: 6px;
}
.vb-text {
  font-size: 14.5px;
  line-height: 1.75;
  margin-bottom: 12px;
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
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.lists {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  margin-top: 18px;
  border-top: 1px dashed var(--c-line);
  padding-top: 16px;
}
.list-block h4 {
  font-size: 13.5px;
  margin-bottom: 8px;
}
.list-block ul {
  margin: 0;
  padding-left: 17px;
  font-size: 13px;
  color: var(--c-ink-2);
  line-height: 1.8;
}
.share-foot {
  margin-top: 22px;
  border-top: 2px solid var(--c-judge-line);
  padding-top: 14px;
}
.share-clerk {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--c-judge);
  margin-bottom: 10px;
}
.share-foot p {
  font-size: 12px;
  color: var(--c-ink-3);
  line-height: 1.7;
  margin: 0 0 10px;
}
.cta {
  font-size: 13.5px;
  font-weight: 600;
}
@media (max-width: 640px) {
  .share-doc {
    padding: 24px 18px;
  }
  .two-col,
  .lists {
    grid-template-columns: 1fr;
  }
}
</style>
