<script setup lang="ts">
// 来源卡片：摘要可展开，原文使用独立链接，避免点“展开”时误跳转
import { computed, ref } from 'vue'
import type { SourceItem } from '@/types'
import { pct, sourceHref } from '@/utils/display'

const props = defineProps<{ source: SourceItem; compact?: boolean }>()
const expanded = ref(false)
const href = computed(() => sourceHref(props.source.url))
const originLabel = computed(() =>
  props.source.origin === 'zhihu' ? '知乎' : props.source.origin === 'web' ? '全网' : props.source.origin,
)
const needsExpand = computed(() => !props.compact && props.source.summary.length > 150)
</script>

<template>
  <article class="src-card">
    <div class="src-head">
      <span class="origin" :class="source.origin">{{ originLabel }}</span>
      <span class="rank">来源可信度 {{ pct(source.rank_score) }}</span>
    </div>
    <a v-if="href" :href="href" target="_blank" rel="noopener noreferrer" class="title">{{ source.title }}</a>
    <div v-else class="title">{{ source.title }}</div>
    <div v-if="!compact" class="summary" :class="{ expanded }">{{ source.summary || '该来源未提供摘要。' }}</div>
    <button v-if="needsExpand" class="expand-btn" @click="expanded = !expanded">
      {{ expanded ? '收起摘要 ↑' : '展开完整摘要 ↓' }}
    </button>
    <div class="meta">
      <span v-if="source.author">{{ source.author }}</span>
      <span v-if="source.published_at">{{ source.published_at.slice(0, 10) }}</span>
      <span v-if="source.vote_count > 0">{{ source.vote_count }} 赞同</span>
      <span v-if="source.comment_count > 0">{{ source.comment_count }} 评论</span>
      <a v-if="href" :href="href" target="_blank" rel="noopener noreferrer" class="link-hint">查看原文 ↗</a>
      <span v-else-if="source.is_demo" class="demo-note">演示来源，无外部链接</span>
    </div>
  </article>
</template>

<style scoped>
.src-card {
  display: block;
  background: #fff;
  border: 1px solid var(--c-line);
  border-radius: var(--radius);
  padding: 12px 15px;
  color: var(--c-ink);
  transition: box-shadow 0.15s, border-color 0.15s;
  align-self: start;
}
.src-card:hover { border-color: #c6c9d4; box-shadow: var(--shadow-card); }
.src-head { display: flex; align-items: center; margin-bottom: 6px; gap: 8px; }
.origin { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 4px; }
.origin.zhihu { background: var(--c-primary-bg); color: var(--c-primary); }
.origin.web { background: #eef7f7; color: #2b7a78; }
.rank { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--c-ink-3); }
.title {
  display: -webkit-box;
  color: var(--c-ink);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.5;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
a.title:hover { color: var(--c-primary); }
.summary {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--c-ink-3);
  line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.summary.expanded { display: block; overflow: visible; }
.expand-btn {
  margin-top: 6px;
  border: none;
  background: none;
  padding: 0;
  color: var(--c-primary);
  font-family: var(--font);
  font-size: 11.5px;
  cursor: pointer;
}
.meta { margin-top: 8px; display: flex; gap: 12px; font-size: 11.5px; color: var(--c-ink-3); flex-wrap: wrap; }
.link-hint { color: var(--c-primary); margin-left: auto; }
.demo-note { color: var(--c-warn); margin-left: auto; }
</style>
