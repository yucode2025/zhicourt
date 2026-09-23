import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { NMessageProvider } from 'naive-ui'

const apiMocks = vi.hoisted(() => ({
  adminProviders: vi.fn(),
  adminProviderConfig: vi.fn(),
  adminSaveProviderConfig: vi.fn(),
  adminQuota: vi.fn(),
  adminTestLlmConfig: vi.fn(),
  adminTestProviderConfig: vi.fn(),
  adminIntegrity: vi.fn(),
}))
vi.mock('@/api/endpoints', () => ({ api: apiMocks }))

import AdminProviders from '@/views/admin/AdminProviders.vue'

describe('AdminProviders', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    apiMocks.adminProviders.mockResolvedValue({ items: [] })
    apiMocks.adminProviderConfig.mockResolvedValue({
    items: [
      {
        key: 'zhihu_api_key',
        description: 'Access Secret',
        is_secret: true,
        value: '••••1234',
        from_db: true,
        set: true,
        group: 'content',
      },
      {
        key: 'zhihu_oauth_app_id',
        description: 'OAuth App ID',
        is_secret: false,
        value: 'old-app',
        from_db: true,
        set: true,
        group: 'oauth',
      },
      {
        key: 'llm_model',
        description: '主接口模型',
        is_secret: false,
        value: 'old-model',
        from_db: true,
        set: true,
        group: 'llm',
      },
    ],
    mode: { zhihu: 'MOCK', web: 'MOCK', llm: 'FALLBACK', oauth: 'REAL' },
    })
    apiMocks.adminSaveProviderConfig.mockResolvedValue({
      ok: true,
      changes: {},
      mode: { zhihu: 'MOCK', web: 'MOCK', llm: 'FALLBACK', oauth: 'REAL' },
    })
  })

  it('explicitly submits an empty DB secret only after clear is clicked', async () => {
    const wrapper = mount(NMessageProvider, {
      slots: { default: AdminProviders },
      attachTo: document.body,
    })
    await flushPromises()

    const clear = wrapper.find('[data-testid="clear-secret-zhihu_api_key"]')
    expect(clear.exists()).toBe(true)
    await clear.trigger('click')
    expect(wrapper.text()).toContain('保存后显式清空数据库 Secret')

    const save = wrapper.findAll('button').find((button) => button.text() === '保存配置')
    expect(save).toBeDefined()
    await save!.trigger('click')
    await flushPromises()

    expect(apiMocks.adminSaveProviderConfig).toHaveBeenCalledWith({ zhihu_api_key: '' })
    wrapper.unmount()
  })

  it('submits only the configuration group whose save button was clicked', async () => {
    const wrapper = mount(NMessageProvider, {
      slots: { default: AdminProviders },
      attachTo: document.body,
    })
    await flushPromises()

    await wrapper.find('[data-testid="config-zhihu_oauth_app_id"] input').setValue('new-app')
    await wrapper.find('[data-testid="config-llm_model"] input').setValue('new-model')

    const saveOauth = wrapper.findAll('button').find((button) => button.text() === '保存 OAuth 配置')
    expect(saveOauth).toBeDefined()
    await saveOauth!.trigger('click')
    await flushPromises()

    expect(apiMocks.adminSaveProviderConfig).toHaveBeenCalledTimes(1)
    expect(apiMocks.adminSaveProviderConfig).toHaveBeenCalledWith({ zhihu_oauth_app_id: 'new-app' })
    wrapper.unmount()
  })
})
