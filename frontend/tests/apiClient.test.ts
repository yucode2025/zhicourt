import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, patch } from '@/api/client'

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'zhicourt_csrf=; Max-Age=0; path=/'
})

describe('API client security and errors', () => {
  it('adds the double-submit CSRF token to PATCH requests', async () => {
    document.cookie = 'zhicourt_csrf=csrf-token; path=/'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await patch('/api/auth/me', { nickname: '新昵称' })

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-token')
    expect(init.credentials).toBe('same-origin')
  })

  it('normalizes structured backend errors without leaking JSON into the UI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { reason: 'state_mismatch', message: '请重新登录' } }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))

    await expect(patch('/api/auth/zhihu/callback', {})).rejects.toMatchObject<Partial<ApiError>>({
      message: '请重新登录',
      code: 'state_mismatch',
      status: 400,
    })
  })
})
