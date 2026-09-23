import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'

const apiMocks = vi.hoisted(() => ({
  zhihuCallback: vi.fn(),
  me: vi.fn(),
  capabilities: vi.fn(),
}))
vi.mock('@/api/endpoints', () => ({ api: apiMocks }))

import ZhihuCallbackView from '@/views/auth/ZhihuCallbackView.vue'
import { useAuthStore } from '@/stores/auth'

describe('ZhihuCallbackView', () => {
  it('accepts authorization_code, restores a safe redirect, and sets the user', async () => {
    const user = {
      id: 'usr_1', username: null, account_kind: 'zhihu' as const,
      nickname: '知乎知友', avatar: '⚖', role: 'user' as const,
      created_at: null, last_login_at: null,
    }
    apiMocks.zhihuCallback.mockResolvedValue({ user, created: true, migrated_cases: 0 })
    apiMocks.me.mockResolvedValue({ user })
    apiMocks.capabilities.mockResolvedValue({ usage_access_policy: 'guest', can_use: true, required_action: 'none', zhihu_oauth: { enabled: true, callback_path: '/login/zhihu' } })
    sessionStorage.setItem('zhicourt_oauth_redirect', '/cases')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/login/zhihu', component: ZhihuCallbackView },
        { path: '/cases', component: { template: '<div>cases</div>' } },
      ],
    })
    await router.push('/login/zhihu?authorization_code=one-time-code&state=secure-state')
    await router.isReady()
    const pinia = createPinia()

    const wrapper = mount(NMessageProvider, {
      slots: { default: ZhihuCallbackView },
      global: { plugins: [pinia, router] },
    })
    await flushPromises()

    expect(apiMocks.zhihuCallback).toHaveBeenCalledWith('one-time-code', 'secure-state')
    expect(useAuthStore(pinia).user?.id).toBe('usr_1')
    expect(router.currentRoute.value.fullPath).toBe('/cases')
    expect(sessionStorage.getItem('zhicourt_oauth_redirect')).toBeNull()
    wrapper.unmount()
  })

  it('does not call the backend when state is missing', async () => {
    apiMocks.zhihuCallback.mockClear()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/login/zhihu', component: ZhihuCallbackView }],
    })
    await router.push('/login/zhihu?authorization_code=one-time-code')
    await router.isReady()

    const wrapper = mount(NMessageProvider, {
      slots: { default: ZhihuCallbackView },
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(apiMocks.zhihuCallback).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('缺少授权参数')
    wrapper.unmount()
  })
})
