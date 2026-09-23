import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { UserInfo } from '@/types'
import { ApiError } from '@/api/client'

const apiMocks = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('@/api/endpoints', () => ({ api: apiMocks }))

import { useAuthStore } from '@/stores/auth'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const user = (id: string): UserInfo => ({
  id,
  username: id,
  account_kind: 'member',
  has_zhihu_oauth: false,
  nickname: id,
  avatar: '⚖',
  role: 'user',
  created_at: null,
  last_login_at: null,
})

describe('auth store request ordering', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
  })

  it('keeps a temporary /auth/me failure distinct from a confirmed anonymous session', async () => {
    apiMocks.me
      .mockRejectedValueOnce(new Error('身份服务暂不可用'))
      .mockResolvedValueOnce({ user: null })
    const store = useAuthStore()

    await store.fetchMe()

    expect(store.user).toBeNull()
    expect(store.loaded).toBe(false)
    expect(store.sessionError).toBe('身份服务暂不可用')

    await store.fetchMe()

    expect(apiMocks.me).toHaveBeenCalledTimes(2)
    expect(store.user).toBeNull()
    expect(store.loaded).toBe(true)
    expect(store.sessionError).toBe('')
  })

  it('commits a confirmed anonymous session when /auth/me returns 401', async () => {
    apiMocks.me.mockRejectedValueOnce(new ApiError('未登录', 401))
    const store = useAuthStore()
    store.setUser(user('expired'))

    await store.fetchMe(true)

    expect(store.user).toBeNull()
    expect(store.loaded).toBe(true)
    expect(store.sessionError).toBe('')
  })

  it('retains the trusted identity when logout fails', async () => {
    const store = useAuthStore()
    store.setUser(user('current'))
    apiMocks.logout.mockRejectedValueOnce(new Error('退出请求失败'))

    await expect(store.logout()).rejects.toThrow('退出请求失败')

    expect(store.user?.id).toBe('current')
    expect(store.loaded).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('deduplicates concurrent fetchMe calls', async () => {
    const request = deferred<{ user: UserInfo }>()
    apiMocks.me.mockReturnValue(request.promise)
    const store = useAuthStore()

    const first = store.fetchMe()
    const second = store.fetchMe()
    expect(apiMocks.me).toHaveBeenCalledTimes(1)

    request.resolve({ user: user('current') })
    await expect(first).resolves.toMatchObject({ id: 'current' })
    await expect(second).resolves.toMatchObject({ id: 'current' })
  })

  it('does not let a stale fetchMe overwrite logout', async () => {
    const meRequest = deferred<{ user: UserInfo }>()
    apiMocks.me.mockReturnValue(meRequest.promise)
    apiMocks.logout.mockResolvedValue({ ok: true })
    const store = useAuthStore()

    const staleFetch = store.fetchMe()
    await store.logout()
    meRequest.resolve({ user: user('stale') })
    await staleFetch

    expect(store.user).toBeNull()
    expect(store.loaded).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('only commits the latest competing login', async () => {
    const first = deferred<{ user: UserInfo }>()
    const second = deferred<{ user: UserInfo }>()
    apiMocks.login.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const store = useAuthStore()

    const oldLogin = store.login('old', 'password')
    const newLogin = store.login('new', 'password')
    second.resolve({ user: user('new') })
    await newLogin
    first.resolve({ user: user('old') })
    await oldLogin

    expect(store.user?.id).toBe('new')
  })
})
