// 展示工具函数
import type { EvidenceType, Stance } from '@/types'

export const evidenceTypeLabels: Record<EvidenceType, string> = {
  fact: '事实',
  opinion: '观点',
  data: '数据',
  case: '案例',
  prediction: '预测',
  assumption: '假设',
  limitation: '局限',
}

export const stanceLabels: Record<Stance, string> = {
  pro: '支持命题',
  con: '反对命题',
  neutral: '中立',
}

export const issueTypeLabels: Record<string, string> = {
  concept_swap: '偷换概念',
  causal_inversion: '因果倒置',
  correlation_not_causation: '相关不等于因果',
  overgeneralization: '过度泛化',
  sample_bias: '样本偏差',
  survivorship_bias: '幸存者偏差',
  insufficient_evidence: '证据不足',
  recency_risk: '证据时效性风险',
  scope_mismatch: '适用范围错误',
  definition_conflict: '定义冲突',
  evidence_argument_mismatch: '论点与证据不匹配',
  missing_counterexample: '缺乏反例',
  source_concentration: '证据来源过于集中',
  data_quality: '数据质量问题',
  opinion_as_fact: '观点被当作事实',
  prediction_as_fact: '预测被当作事实',
  other: '其他问题',
}

export const challengeTypeLabels: Record<string, string> = {
  fact: '事实质疑',
  logic: '逻辑质疑',
  evidence: '证据质疑',
  definition: '定义质疑',
  scope: '范围质疑',
  source: '来源质疑',
  other: '一般质询',
}

export const conclusionStanceLabels: Record<string, string> = {
  prosecution: '倾向支持命题',
  defense: '倾向反对命题',
  conditional: '条件性判断',
  insufficient: '证据不足',
}

export function preferredScrollBehavior(reducedMotion: boolean): ScrollBehavior {
  return reducedMotion ? 'auto' : 'smooth'
}

export function fmtTime(iso: string): string {
  if (!iso) return '—'
  // 无时区的后端时间按 UTC；已有 Z/数值时区偏移不能重复追加 Z。
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function pct(x: number): string {
  return `${Math.round(x * 100)}%`
}

/** 来源链接仅接受无凭据的绝对 HTTP(S) URL。 */
export function sourceHref(url: string): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    if (!['http:', 'https:'].includes(parsed.protocol)) return null
    if (parsed.username || parsed.password || !parsed.hostname) return null
    return parsed.href
  } catch {
    return null
  }
}
