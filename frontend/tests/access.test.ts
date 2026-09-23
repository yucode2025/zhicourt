import { beforeEach, describe, expect, it } from 'vitest'
import { policyAllows, safeRedirect, saveOAuthIntent, scalarQuery, takeOAuthIntent } from '@/utils/access'

const member = { id: 'u', username: 'u', account_kind: 'member' as const, has_zhihu_oauth: false, nickname: 'U', avatar: '⚖', role: 'user' as const, created_at: null, last_login_at: null }

describe('access policy utilities', () => {
  beforeEach(() => sessionStorage.clear())

  it('only accepts same-origin path redirects', () => {
    expect(safeRedirect('/cases?q=1')).toBe('/cases?q=1')
    expect(safeRedirect('//evil.example')).toBe('/')
    expect(safeRedirect('/\\evil.example')).toBe('/')
    expect(safeRedirect(['/', '/cases'])).toBe('/')
  })

  it('requires a scalar callback query value', () => {
    expect(scalarQuery('code')).toBe('code')
    expect(scalarQuery(['one', 'two'])).toBeNull()
    expect(scalarQuery('')).toBeNull()
  })

  it('applies guest, authenticated and zhihu policies', () => {
    expect(policyAllows('guest', null)).toBe(true)
    expect(policyAllows('authenticated', member)).toBe(true)
    expect(policyAllows('zhihu', member)).toBe(false)
    expect(policyAllows('zhihu', { ...member, has_zhihu_oauth: true })).toBe(true)
    expect(policyAllows('zhihu', { ...member, account_kind: 'zhihu' })).toBe(false)
  })

  it('round-trips oauth intent and consumes it once', () => {
    saveOAuthIntent({ mode: 'link', redirect: '//evil.example' })
    expect(takeOAuthIntent()).toEqual({ mode: 'link', redirect: '/' })
    expect(takeOAuthIntent()).toBeNull()
  })
})
