// 统一 API 客户端：错误归一化 + 超时
export class ApiError extends Error {
  code: string
  status: number

  constructor(message: string, status: number, code = 'api_error') {
    super(message)
    this.status = status
    this.code = code
  }
}

function parseErrorBody(body: unknown, fallback: string): { message: string; code?: string } {
  if (!body || typeof body !== 'object') return { message: fallback }
  const value = body as { detail?: unknown; error?: { message?: unknown; code?: unknown } }
  if (typeof value.detail === 'string') return { message: value.detail }
  if (value.detail && typeof value.detail === 'object') {
    const detail = value.detail as { message?: unknown; reason?: unknown; code?: unknown }
    return {
      message: typeof detail.message === 'string' ? detail.message : fallback,
      code: typeof detail.code === 'string'
        ? detail.code
        : typeof detail.reason === 'string' ? detail.reason : undefined,
    }
  }
  return {
    message: typeof value.error?.message === 'string' ? value.error.message : fallback,
    code: typeof value.error?.code === 'string' ? value.error.code : undefined,
  }
}

const BASE = ''
const CSRF_COOKIE = 'zhicourt_csrf'

function csrfToken(): string | undefined {
  const prefix = `${CSRF_COOKIE}=`
  const item = document.cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith(prefix))
  if (!item) return undefined
  try {
    return decodeURIComponent(item.slice(prefix.length))
  } catch {
    return undefined
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 60000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const method = (init?.method ?? 'GET').toUpperCase()
    const token = !['GET', 'HEAD', 'OPTIONS'].includes(method) ? csrfToken() : undefined
    const headers = new Headers(init?.headers)
    headers.set('Content-Type', 'application/json')
    if (token) headers.set('X-CSRF-Token', token)
    const resp = await fetch(BASE + path, {
      ...init,
      credentials: 'same-origin',
      signal: controller.signal,
      headers,
    })
    if (!resp.ok) {
      let detail = `请求失败（${resp.status}）`
      let code = resp.status === 429 ? 'rate_limited' : 'api_error'
      try {
        const body = await resp.json()
        const parsed = parseErrorBody(body, detail)
        detail = parsed.message
        if (parsed.code) code = parsed.code
      } catch {
        /* ignore */
      }
      throw new ApiError(detail, resp.status, code)
    }
    if (resp.status === 204) return undefined as T
    return (await resp.json()) as T
  } catch (e) {
    if (e instanceof ApiError) throw e
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError('请求超时，请稍后重试', 408, 'timeout')
    }
    throw new ApiError('网络连接失败，请检查后端服务是否可用', 0, 'network')
  } finally {
    clearTimeout(timer)
  }
}

export const get = <T>(path: string, timeout = 60000) => request<T>(path, { method: 'GET' }, timeout)
export const post = <T>(path: string, body?: unknown, timeout = 60000) =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }, timeout)
export const patch = <T>(path: string, body?: unknown, timeout = 60000) =>
  request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }, timeout)
export const del = <T>(path: string, timeout = 60000) => request<T>(path, { method: 'DELETE' }, timeout)
