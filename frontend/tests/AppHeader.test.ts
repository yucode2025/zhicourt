import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'
import type { UserInfo } from '@/types'

const apiMocks = vi.hoisted(() => ({ me: vi.fn(), logout: vi.fn() }))
vi.mock('@/api/endpoints', () => ({ api: apiMocks }))

import AppHeader from '@/components/common/AppHeader.vue'
import { useAuthStore } from '@/stores/auth'

const currentUser: UserInfo = {
  id: 'current',
  username: 'current',
  account_kind: 'member',
  has_zhihu_oauth: false,
  nickname: '当前用户',
  avatar: '⚖',
  role: 'user',
  created_at: null,
  last_login_at: null,
}

describe('AppHeader', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    apiMocks.me.mockRejectedValue(new Error('guest'))
  })

  it('keeps desktop and mobile navigation mutually exclusive with complete guest menu data', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: ['/', '/cases', '/login', '/register', '/case/new'].map((path) => ({ path, component: { template: '<div />' } })),
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(NMessageProvider, {
      slots: { default: AppHeader },
      global: { plugins: [createPinia(), router] },
      attachTo: document.body,
    })

    expect(wrapper.find('.desktop-actions').exists()).toBe(true)
    const mobileButton = wrapper.find('.mobile-menu-btn')
    expect(mobileButton.attributes('aria-haspopup')).toBe('menu')
    expect(mobileButton.attributes('aria-expanded')).toBe('false')
    expect(wrapper.text()).toContain('登录')
    expect(wrapper.text()).toContain('注册')
    expect(wrapper.html()).not.toMatch(/<a[^>]*>\s*<button/i)
    wrapper.unmount()
  })

  it('keeps the user signed in and shows an actionable error when logout fails', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: ['/', '/profile'].map((path) => ({ path, component: { template: '<div />' } })),
    })
    await router.push('/')
    await router.isReady()
    const pinia = createPinia()
    const auth = useAuthStore(pinia)
    auth.setUser(currentUser)
    apiMocks.logout.mockRejectedValueOnce(new Error('退出请求失败，请检查网络后重试'))

    const wrapper = mount(NMessageProvider, {
      slots: { default: AppHeader },
      global: { plugins: [pinia, router] },
      attachTo: document.body,
    })

    await wrapper.find('.mobile-menu-btn').trigger('click')
    const logout = wrapper.findAll('.mobile-menu button').find((button) => button.text() === '退出登录')
    expect(logout).toBeDefined()
    await logout!.trigger('click')
    await flushPromises()

    expect(auth.user?.id).toBe('current')
    expect(router.currentRoute.value.path).toBe('/')
    expect(document.body.textContent).toContain('退出请求失败，请检查网络后重试')
    wrapper.unmount()
  })
})
