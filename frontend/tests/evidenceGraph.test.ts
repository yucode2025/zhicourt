import { describe, expect, it } from 'vitest'
import { graphEntryAvailable, graphUnavailableReason, hasCoreEvidenceLinks, resolveArgumentClaims } from '@/utils/evidenceGraph'
import type { ArgumentItem, CaseDetail } from '@/types'

const base = {
  status: 'verdict_ready',
  evidence: [{ id: 'ev-1' }, { id: 'ev-2' }],
  arguments: [],
  claims: [
    { id: 'claim-shared', side: 'pro', text: '共享证据主张', evidence_ids: ['ev-1'] },
    { id: 'claim-stance-only', side: 'pro', text: '仅立场相同', evidence_ids: ['ev-2'] },
  ],
} as unknown as CaseDetail

const argument: ArgumentItem = {
  id: 'arg-1', side: 'prosecution', title: '论证', body: '内容', evidence_ids: ['ev-1'], strength: 0.8,
}

describe('graph availability', () => {
  it.each([
    ['created', '尚未开始审理'],
    ['queued', '正在排队'],
    ['running', '正在审理'],
    ['failed', '审理失败'],
  ] as const)('gates %s deep links with a state-specific reason', (status, message) => {
    const kase = { ...base, status } as CaseDetail
    expect(graphEntryAvailable(kase)).toBe(false)
    expect(graphUnavailableReason(kase)).toContain(message)
  })

  it('gates ready cases with zero evidence and allows ready cases with evidence', () => {
    expect(graphUnavailableReason({ ...base, evidence: [] } as unknown as CaseDetail)).toContain('没有证据')
    expect(graphEntryAvailable(base)).toBe(true)
  })

  it('distinguishes an empty core from isolated evidence', () => {
    expect(hasCoreEvidenceLinks(base)).toBe(false)
    expect(hasCoreEvidenceLinks({ ...base, arguments: [argument] } as CaseDetail)).toBe(true)
    expect(hasCoreEvidenceLinks({ ...base, arguments: [{ ...argument, evidence_ids: ['missing'] }] } as CaseDetail)).toBe(false)
  })
})

describe('resolveArgumentClaims', () => {
  it('never links claims only because stance matches', () => {
    const result = resolveArgumentClaims(base, argument)
    expect(result.kind).toBe('inferred')
    expect(result.items.map((claim) => claim.id)).toEqual(['claim-shared'])
  })

  it('uses explicit claim_ids when backend provides them', () => {
    const result = resolveArgumentClaims(base, { ...argument, claim_ids: ['claim-stance-only', 'missing'] })
    expect(result.kind).toBe('explicit')
    expect(result.items.map((claim) => claim.id)).toEqual(['claim-stance-only'])
    expect(result.missingIds).toEqual(['missing'])
  })

  it('does not infer anything when explicit claim_ids is empty', () => {
    const result = resolveArgumentClaims(base, { ...argument, claim_ids: [] })
    expect(result).toEqual({ kind: 'explicit', items: [], missingIds: [] })
  })
})
