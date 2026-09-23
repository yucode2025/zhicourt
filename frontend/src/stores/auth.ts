// 认证状态 store：当前用户 + 登录/登出 + 头部状态
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/client'
import type { Capabilities, UserInfo } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const loaded = ref(false)
  const loading = ref(false)
  const sessionError = ref('')
  const capabilities = ref<Capabilities | null>(null)
  const capabilitiesLoaded = ref(false)
  const capabilitiesLoading = ref(false)
  const capabilitiesError = ref('')
  let epoch = 0
  let capabilitiesEpoch = 0
  let meInflight: Promise<UserInfo | null> | null = null
  let capabilitiesInflight: Promise<Capabilities | null> | null = null

  const isLoggedIn = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function invalidateCapabilities() {
    capabilitiesEpoch += 1
    capabilitiesInflight = null
    capabilitiesLoading.value = false
    capabilities.value = null
    capabilitiesLoaded.value = false
    capabilitiesError.value = ''
  }

  function commit(nextUser: UserInfo | null) {
    epoch += 1
    user.value = nextUser
    loaded.value = true
    sessionError.value = ''
    invalidateCapabilities()
  }

  async function fetchMe(force = false) {
    if (loaded.value && !force) return user.value
    if (meInflight && !force) return meInflight

    const requestEpoch = ++epoch
    loading.value = true
    sessionError.value = ''
    const request = api.me()
      .then((res) => {
        if (requestEpoch === epoch) {
          user.value = res.user
          loaded.value = true
          sessionError.value = ''
        }
        return requestEpoch === epoch ? res.user : user.value
      })
      .catch((error: unknown) => {
        if (requestEpoch === epoch) {
          if (error instanceof ApiError && error.status === 401) {
            // 401 是服务端对匿名会话的明确判定，可以安全清除本地身份。
            user.value = null
            loaded.value = true
            sessionError.value = ''
          } else {
            // 网络或服务端故障不等于匿名会话。保留最后一次可信身份，
            // 首次加载失败时也保持 loaded=false，使恢复后的请求可以重试。
            sessionError.value = error instanceof Error ? error.message : '登录状态查询失败'
          }
        }
        return user.value
      })
      .finally(() => {
        if (meInflight === request) meInflight = null
        if (requestEpoch === epoch) loading.value = false
      })
    meInflight = request
    return request
  }

  async function fetchCapabilities(force = false) {
    if (capabilitiesLoaded.value && !force) return capabilities.value
    if (capabilitiesInflight && !force) return capabilitiesInflight
    const requestEpoch = ++capabilitiesEpoch
    capabilitiesLoading.value = true
    capabilitiesError.value = ''
    const request = api.capabilities()
      .then((res) => {
        if (requestEpoch === capabilitiesEpoch) {
          capabilities.value = res
          capabilitiesLoaded.value = true
        }
        return requestEpoch === capabilitiesEpoch ? res : capabilities.value
      })
      .catch((error: unknown) => {
        if (requestEpoch === capabilitiesEpoch) {
          capabilities.value = null
          capabilitiesLoaded.value = false
          capabilitiesError.value = error instanceof Error ? error.message : '访问策略加载失败'
        }
        return null
      })
      .finally(() => {
        if (capabilitiesInflight === request) capabilitiesInflight = null
        if (requestEpoch === capabilitiesEpoch) capabilitiesLoading.value = false
      })
    capabilitiesInflight = request
    return request
  }

  async function refreshSession() {
    await Promise.all([fetchMe(true), fetchCapabilities(true)])
    return user.value
  }

  async function login(username: string, password: string) {
    const operationEpoch = ++epoch
    loading.value = false
    const res = await api.login(username, password)
    if (operationEpoch === epoch) {
      user.value = res.user
      loaded.value = true
      sessionError.value = ''
      invalidateCapabilities()
    }
    return res
  }

  async function register(username: string, password: string, nickname?: string) {
    const operationEpoch = ++epoch
    loading.value = false
    const res = await api.register(username, password, nickname)
    if (operationEpoch === epoch) {
      user.value = res.user
      loaded.value = true
      sessionError.value = ''
      invalidateCapabilities()
    }
    return res
  }

  async function logout() {
    const operationEpoch = ++epoch
    loading.value = true
    try {
      await api.logout()
      if (operationEpoch === epoch) {
        user.value = null
        loaded.value = true
        sessionError.value = ''
        invalidateCapabilities()
      }
    } finally {
      if (operationEpoch === epoch) loading.value = false
    }
  }

  function setUser(u: UserInfo) {
    commit(u)
  }

  return {
    user, loaded, loading, sessionError, isLoggedIn, isAdmin,
    capabilities, capabilitiesLoaded, capabilitiesLoading, capabilitiesError,
    fetchMe, fetchCapabilities, refreshSession, login, register, logout, setUser,
  }
})
