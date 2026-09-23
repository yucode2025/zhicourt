import { get, post, patch, del } from './client'
import type {
  AdminCase, AdminDashboard, AdminUser, AuditItem, CaseDetail, CaseSummary, HackathonContentItem,
  HackathonContentKind, HotItem,
  IntegrityReport, LearningProfile, Paged, PlanInfo, ProviderStatus, SystemSettingItem,
  UsageProvider, UserInfo, UserOverview, UserQuestionCard, UserQuestionItem, VerdictCard, VerdictItem, Capabilities,
} from '@/types'

export const api = {
  // ---- 庭审 ----
  plan: (question: string) => post<PlanInfo>('/api/plan', { question }),
  createCase: (question: string, proposition?: string, title?: string) =>
    post<CaseSummary>('/api/cases', { question, proposition, title }),
  startCase: (id: string) => post<CaseSummary>(`/api/cases/${id}/start`),
  retryCase: (id: string) => post<CaseSummary>(`/api/cases/${id}/retry`),
  publishCase: (id: string) => post<CaseSummary>(`/api/cases/${id}/publish`),
  unpublishCase: (id: string) => post<CaseSummary>(`/api/cases/${id}/unpublish`),
  deleteCase: (id: string) => del<void>(`/api/cases/${id}`),
  getCase: (id: string) => get<CaseDetail>(`/api/cases/${id}`),
  listCases: (q = '', status = '') =>
    get<CaseSummary[]>(`/api/cases?q=${encodeURIComponent(q)}&status=${status}`),
  hot: () => get<{ items: HotItem[]; is_demo: boolean; error?: string }>('/api/hot'),
  hackathonContent: (kind: HackathonContentKind, limit = 12) =>
    get<{ kind: HackathonContentKind; items: HackathonContentItem[]; error?: string }>(
      `/api/hackathon/content?kind=${kind}&limit=${limit}`,
      15000,
    ),
  usage: () => get<{ providers: Record<string, UsageProvider> }>('/api/usage'),
  challenge: (caseId: string, target: string, text: string, targetRefId?: string) =>
    post<UserQuestionItem>(`/api/cases/${caseId}/questions`, {
      target, text, target_ref_id: targetRefId ?? null,
    }),
  pollEvents: (caseId: string, after: number) =>
    get<{ events: { i: number; stage: string; message: string }[]; status: string; index: number }>(
      `/api/cases/${caseId}/events?after=${after}`, 15000)

  ,
  // ---- 认证 ----
  register: (username: string, password: string, nickname?: string) =>
    post<{ user: UserInfo; migrated_cases: number }>('/api/auth/register', { username, password, nickname }),
  login: (username: string, password: string) =>
    post<{ user: UserInfo; migrated_cases: number }>('/api/auth/login', { username, password }),
  logout: () => post<{ ok: boolean }>('/api/auth/logout'),
  me: () => get<{ user: UserInfo | null }>('/api/auth/me'),
  capabilities: () => get<Capabilities>('/api/capabilities'),
  authProviders: () => get<{ zhihu: { enabled: boolean; callback_path: string } }>('/api/auth/providers'),
  zhihuAuthorize: () => get<{ authorize_url: string }>('/api/auth/zhihu/authorize', 15000),
  zhihuLinkAuthorize: (password: string) =>
    post<{ authorize_url: string }>('/api/auth/zhihu/link/authorize', { password }, 15000),
  zhihuCallback: (code: string, state: string) =>
    post<{ user: UserInfo; created: boolean; linked?: boolean; transferred?: boolean; migrated_cases: number }>('/api/auth/zhihu/callback', { code, state }, 20000),
  updateProfile: (data: { nickname?: string; avatar?: string }) =>
    patch<{ user: UserInfo }>('/api/auth/me', data),
  changePassword: (old_password: string, new_password: string) =>
    post<{ ok: boolean }>('/api/auth/me/password', { old_password, new_password }),
  overview: () => get<UserOverview>('/api/auth/me/overview'),
  myCases: () =>
    get<{ id: string; title: string; proposition: string; status: string; is_demo: boolean; is_public: boolean; public_id: string | null; created_at: string | null }[]>(
      '/api/auth/me/cases',
    ),
  myVerdicts: (listType: 'recent' | 'favorites' = 'recent') =>
    get<VerdictCard[]>(`/api/auth/me/verdicts?list_type=${listType}`),
  myQuestions: () => get<UserQuestionCard[]>('/api/auth/me/questions'),
  learning: () => get<LearningProfile>('/api/auth/me/learning'),

  // ---- 收藏 ----
  favorite: (caseId: string) => post<{ ok: boolean; is_favorite: boolean }>(`/api/cases/${caseId}/favorite`),
  unfavorite: (caseId: string) => del<{ ok: boolean; is_favorite: boolean }>(`/api/cases/${caseId}/favorite`),
  favoriteStatus: (caseId: string) => get<{ is_favorite: boolean }>(`/api/cases/${caseId}/favorite`),

  // ---- 分享 ----
  getShared: (publicId: string) =>
    get<{
      title: string; proposition: string; status: string; is_demo: boolean; engine_mode: string;
      source_mode: CaseDetail['source_mode']; execution_summary: CaseDetail['execution_summary'];
      created_at: string | null;
      sources: CaseDetail['sources']; evidence: CaseDetail['evidence'];
      arguments: CaseDetail['arguments']; cross_examinations: CaseDetail['cross_examinations'];
      verdict: VerdictItem | null;
    }>(`/api/share/${publicId}`),

  // ---- 管理后台 ----
  adminDashboard: () => get<AdminDashboard>('/api/admin/dashboard'),
  adminUsers: (q = '', page = 1, size = 20) =>
    get<Paged<AdminUser>>(`/api/admin/users?q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
  adminSetUserStatus: (id: string, status: 'active' | 'disabled') =>
    post<{ ok: boolean }>(`/api/admin/users/${id}/status`, { status }),
  adminSetUserRole: (id: string, role: 'user' | 'admin') =>
    post<{ ok: boolean }>(`/api/admin/users/${id}/role`, { role }),
  adminCases: (status = '', q = '', page = 1, size = 20) =>
    get<Paged<AdminCase>>(`/api/admin/cases?status=${status}&q=${encodeURIComponent(q)}&page=${page}&size=${size}`),
  adminCaseDetail: (id: string) =>
    get<{ id: string; title: string; status: string; engine_mode: string; owner: string; error_message: string | null; agent_runs: { agent: string; status: string; mode: string; duration_ms: number; error: string | null }[] }>(`/api/admin/cases/${id}/detail`),
  adminRetryCase: (id: string) => post<{ ok: boolean }>(`/api/admin/cases/${id}/retry`),
  adminApiUsage: () =>
    get<{ today: string; providers: Record<string, UsageProvider & { near_limit: boolean }>; history: Record<string, { date: string; calls: number; cache_hits: number; failures: number }[]> }>('/api/admin/api-usage'),
  adminProviders: () => get<{ items: ProviderStatus[] }>('/api/admin/providers'),
  adminProviderConfig: () =>
    get<{ items: { key: string; description: string; is_secret: boolean; value: string; from_db: boolean; set: boolean; group: string }[]; mode: { zhihu: string; web: string; llm: string; oauth: string } }>(
      '/api/admin/providers/config',
    ),
  adminSaveProviderConfig: (fields: Record<string, string>) =>
    post<{ ok: boolean; changes: Record<string, string>; mode: { zhihu: string; web: string; llm: string; oauth: string } }>(
      '/api/admin/providers/config',
      { fields },
    ),
  adminTestLlmConfig: (fields: Record<string, string>) =>
    post<{ ok: boolean; latency_ms?: number; message?: string; error?: string }>(
      '/api/admin/providers/config/test-llm',
      { fields },
    ),
  adminTestProviderConfig: (fields: Record<string, string>) =>
    post<{ ok: boolean; target: string; latency_ms?: number; message?: string; error?: string; hint?: string }>(
      '/api/admin/providers/config/test',
      { fields },
    ),
  adminQuota: () =>
    get<{ available: boolean; reason?: string; cached?: boolean; stale?: boolean; updated_at?: string; items?: { APIID: string; APIName: string; TotalQuota: number; TotalUsed: number; RemainingQuota: number }[] }>(
      '/api/admin/providers/quota',
      6500,
    ),
  adminSettings: () =>
    get<{ settings: SystemSettingItem[]; secrets: { name: string; status: string }[] }>('/api/admin/settings'),
  adminSetSetting: (key: string, value: unknown) => post<{ ok: boolean }>(`/api/admin/settings/${key}`, { value }),
  adminAudit: (page = 1, size = 20) => get<Paged<AuditItem>>(`/api/admin/audit?page=${page}&size=${size}`),
  adminIntegrity: () => get<IntegrityReport>('/api/admin/integrity'),
}
