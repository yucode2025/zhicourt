import type { LocationQueryValue, RouteLocationNormalized } from 'vue-router'
import type { AccessPolicy, UserInfo } from '@/types'

export const OAUTH_INTENT_KEY = 'zhicourt_oauth_intent'
export const LEGACY_OAUTH_REDIRECT_KEY = 'zhicourt_oauth_redirect'

export type OAuthIntent = { mode: 'login' | 'link'; redirect: string }

export function safeRedirect(value: unknown, fallback = '/'): string {
  if (Array.isArray(value)) return fallback
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return fallback
  if (value.includes('\\') || /[\u0000-\u001f\u007f]/.test(value)) return fallback
  return value
}

export function scalarQuery(value: LocationQueryValue | LocationQueryValue[] | undefined): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

export function hasZhihuIdentity(user: UserInfo | null): boolean {
  return user?.has_zhihu_oauth === true
}

export function policyAllows(policy: AccessPolicy, user: UserInfo | null): boolean {
  if (policy === 'guest') return true
  if (policy === 'authenticated') return user !== null
  return hasZhihuIdentity(user)
}

export function isAccessExemptRoute(to: RouteLocationNormalized): boolean {
  if (to.meta.accessExempt) return true
  return to.path === '/profile' || to.path.startsWith('/admin') || to.path.startsWith('/share/')
    || to.path === '/login' || to.path === '/register' || to.path === '/login/zhihu'
}

export function saveOAuthIntent(intent: OAuthIntent): void {
  sessionStorage.setItem(OAUTH_INTENT_KEY, JSON.stringify({ ...intent, redirect: safeRedirect(intent.redirect) }))
  sessionStorage.removeItem(LEGACY_OAUTH_REDIRECT_KEY)
}

export function takeOAuthIntent(): OAuthIntent | null {
  const raw = sessionStorage.getItem(OAUTH_INTENT_KEY)
  sessionStorage.removeItem(OAUTH_INTENT_KEY)
  const legacy = sessionStorage.getItem(LEGACY_OAUTH_REDIRECT_KEY)
  sessionStorage.removeItem(LEGACY_OAUTH_REDIRECT_KEY)
  if (!raw) return legacy ? { mode: 'login', redirect: safeRedirect(legacy) } : null
  try {
    const value = JSON.parse(raw) as Partial<OAuthIntent>
    if ((value.mode === 'login' || value.mode === 'link') && typeof value.redirect === 'string') {
      return { mode: value.mode, redirect: safeRedirect(value.redirect) }
    }
  } catch {
    // 损坏或被篡改的浏览器状态不可用于回跳。
  }
  return null
}
