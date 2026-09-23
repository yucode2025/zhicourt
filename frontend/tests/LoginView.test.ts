import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'

const apiMocks = vi.hoisted(() => ({
  login: vi.fn(),
  capabilities: vi.fn().mockResolvedValue({ usage_access_policy: 'guest', can_use: true, required_action: 'none', zhihu_oauth: { enabled: false, callback_path: '/login/zhihu' } }),
  authProviders: vi.fn().mockResolvedValue({ zhihu: { enabled: false, callback_path: '/login/zhihu' } }),
  zhihuAuthorize: vi.fn(),
}))
vi.mock('@/api/endpoints', () => ({ api: apiMocks }))

import LoginView from '@/views/auth/LoginView.vue'

describe('LoginView', () => {
  it('uses one native form submission and blocks rapid re-entry', async () => {
    let resolveLogin!: (value: unknown) => void
    apiMocks.login.mockReturnValue(new Promise((resolve) => { resolveLogin = resolve }))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/login', component: LoginView }, { path: '/register', component: { template: '<div />' } }, { path: '/', component: { template: '<div />' } }] })
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(NMessageProvider, {
      slots: { default: LoginView },
      global: { plugins: [createPinia(), router] },
      attachTo: document.body,
    })
    const form = wrapper.find('form')
    expect(form.exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('使用知乎登录')

    await wrapper.find('input[name="username"]').setValue('alice')
    await wrapper.find('input[name="password"]').setValue('password')
    await form.trigger('submit')
    await form.trigger('submit')
    expect(apiMocks.login).toHaveBeenCalledTimes(1)

    resolveLogin({ user: { id: '1', username: 'alice', nickname: 'Alice', avatar: '⚖', role: 'user' } })
    await Promise.resolve()
    wrapper.unmount()
  })
})
