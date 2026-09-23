import { describe, expect, it } from 'vitest'
import { fmtTime, preferredScrollBehavior, sourceHref } from '@/utils/display'

describe('fmtTime', () => {
  it('keeps explicit offsets and UTC timestamps equivalent', () => {
    expect(fmtTime('2026-09-11T08:00:00+08:00')).toBe(fmtTime('2026-09-11T00:00:00Z'))
    expect(fmtTime('2026-09-11T08:00:00+0800')).toBe(fmtTime('2026-09-11T00:00:00Z'))
    expect(fmtTime('2026-09-11T00:00:00')).toBe(fmtTime('2026-09-11T00:00:00Z'))
  })
})

describe('preferredScrollBehavior', () => {
  it('honors reduced motion without disabling the scroll itself', () => {
    expect(preferredScrollBehavior(true)).toBe('auto')
    expect(preferredScrollBehavior(false)).toBe('smooth')
  })
})

describe('sourceHref', () => {
  it.each([
    '', 'demo://source', '/relative/path', '//example.com/path',
    'javascript:alert(1)', 'data:text/html,hello', 'ftp://example.com/file',
    'https://user:secret@example.com/path', 'http://user@example.com/',
  ])('rejects unsafe or non-absolute URL %s', (url) => {
    expect(sourceHref(url)).toBeNull()
  })

  it.each([
    ['https://example.com/a?q=1', 'https://example.com/a?q=1'],
    ['http://example.com', 'http://example.com/'],
  ])('accepts absolute credential-free HTTP(S) URL', (url, expected) => {
    expect(sourceHref(url)).toBe(expected)
  })
})
