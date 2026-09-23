import type { ArgumentItem, CaseDetail, ClaimItem } from '@/types'

export interface ArgumentClaimResolution {
  kind: 'explicit' | 'inferred'
  items: ClaimItem[]
  missingIds: string[]
}

export function graphUnavailableReason(kase: CaseDetail | null): string {
  if (!kase) return ''
  if (kase.status === 'created') return '案件尚未开始审理，暂无证据图谱。'
  if (kase.status === 'queued') return '案件正在排队，图谱尚未生成。'
  if (kase.status === 'running') return '案件正在审理，图谱尚未完成，请稍后再查看。'
  if (kase.status === 'failed') return '案件审理失败，无法展示完整图谱。'
  if (kase.status !== 'verdict_ready') return '案件状态异常，图谱暂不可用。'
  if (!kase.evidence.length) return '本案没有证据，无法形成证据链路。'
  return ''
}

export function graphEntryAvailable(kase: CaseDetail | null): boolean {
  return !!kase && !graphUnavailableReason(kase)
}

export function hasCoreEvidenceLinks(kase: CaseDetail | null): boolean {
  if (!kase) return false
  const evidenceIds = new Set(kase.evidence.map((item) => item.id))
  return kase.arguments.some((argument) => argument.evidence_ids.some((id) => evidenceIds.has(id)))
}

/**
 * claim_ids 是唯一明确的 Argument→Claim 关系。
 * 旧数据没有 claim_ids 时，只允许按双方实际引用的 evidence_ids 交集推断，并明确标记为 inferred；
 * 不得使用 stance/side 相同来制造关系。
 */
export function resolveArgumentClaims(kase: CaseDetail, argument: ArgumentItem): ArgumentClaimResolution {
  if (Array.isArray(argument.claim_ids)) {
    return {
      kind: 'explicit',
      items: argument.claim_ids
        .map((id) => kase.claims.find((claim) => claim.id === id))
        .filter((item): item is ClaimItem => !!item),
      missingIds: argument.claim_ids.filter((id) => !kase.claims.some((claim) => claim.id === id)),
    }
  }
  return {
    kind: 'inferred',
    items: kase.claims.filter((claim) => claim.evidence_ids.some((id) => argument.evidence_ids.includes(id))),
    missingIds: [],
  }
}
