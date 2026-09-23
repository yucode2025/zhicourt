// ZhiCourt 领域类型定义（与后端 Pydantic Schema 对应）

export interface SourceItem {
  id: string
  origin: string // zhihu | web
  kind: string
  title: string
  url: string
  author: string
  summary: string
  published_at: string
  vote_count: number
  comment_count: number
  rank_score: number
  independence_score: number
  is_demo: boolean
}

export type EvidenceType = 'fact' | 'opinion' | 'data' | 'case' | 'prediction' | 'assumption' | 'limitation'
export type Stance = 'pro' | 'con' | 'neutral'

export interface EvidenceItem {
  id: string
  source_id: string
  claim: string
  stance: Stance
  evidence_type: EvidenceType
  summary: string
  quoted_fragment: string | null
  strength: number
  limitations: string[]
}

export interface ClaimItem {
  id: string
  side: 'pro' | 'con' | 'court' | string
  text: string
  evidence_ids: string[]
}

export interface ArgumentItem {
  id: string
  side: 'prosecution' | 'defense' | 'court'
  title: string
  body: string
  evidence_ids: string[]
  /** 新版后端可直接给出 Claim 引用；旧数据缺省时前端仅以证据交集标记为“推断关系”。 */
  claim_ids?: string[]
  strength: number
}

export interface AgentRunItem {
  id: string
  agent: string
  status: string
  mode: 'llm' | 'heuristic' | 'mixed' | 'provider' | string
  output_summary: string
  error: string | null
  duration_ms: number
}

export interface CrossExamItem {
  id: string
  target_side: string
  issue_type: string
  severity: 'low' | 'medium' | 'high'
  description: string
  related_evidence_ids: string[]
}

export interface UserQuestionItem {
  id: string
  target: string
  target_ref_id: string | null
  text: string
  challenge_type: string
  response: string
  related_evidence_ids: string[]
  created_at: string
}

export interface VerdictItem {
  id: string
  conclusion: string
  conclusion_stance: 'prosecution' | 'defense' | 'conditional' | 'insufficient'
  confidence: number
  prosecution_summary: string
  defense_summary: string
  shared_facts: string[]
  core_disputes: string[]
  strongest_evidence_ids: string[]
  strongest_counter_evidence_ids: string[]
  evidence_gaps: string[]
  definition_conflicts: string[]
  unknowns: string[]
  verdict_changers: string[]
  next_questions: string[]
  cross_exam_summary: string
}

export interface PlanInfo {
  title: string
  proposition: string
  key_concepts: string[]
  disputes: string[]
  sub_questions: string[]
  search_queries: string[]
  suitable: boolean
  needs_rewrite: boolean
  input_classification?: 'calculation' | 'temporal_fact' | 'operation' | 'chat' | 'invalid' | 'debatable'
  rejection_reason?: string | null
  mode: string
}

export interface ProgressEvent {
  i: number
  ts: number
  stage: string
  message: string
}

export type CaseStatus = 'created' | 'queued' | 'running' | 'verdict_ready' | 'failed'

export interface CaseSummary {
  id: string
  title: string
  proposition: string
  status: CaseStatus
  current_stage: string
  engine_mode: string
  source_mode: 'real' | 'mixed' | 'mock' | 'none'
  execution_summary: {
    engine_resolved?: boolean
    agent_modes?: Record<string, string>
    real_sources?: number
    demo_sources?: number
  }
  is_demo: boolean
  is_public: boolean
  public_id: string | null
  progress_index: number
  created_at: string
  error_message: string | null
}

export interface CaseDetail extends CaseSummary {
  original_question: string
  plan: PlanInfo | null
  progress_events: ProgressEvent[]
  is_owner: boolean
  sources: SourceItem[]
  evidence: EvidenceItem[]
  claims: ClaimItem[]
  arguments: ArgumentItem[]
  agent_runs: AgentRunItem[]
  cross_examinations: CrossExamItem[]
  user_questions: UserQuestionItem[]
  verdict: VerdictItem | null
}

export interface HotItem {
  title: string
  url: string
  heat: number
  excerpt: string
  is_demo: boolean
}

export type HackathonContentKind = 'knowledge' | 'story'

export interface HackathonContentItem {
  work_id: string
  title: string
  description: string
  labels: string[]
  artwork: string
  tab_artwork: string
}

export interface UsageProvider {
  calls: number
  cache_hits: number
  failures: number
  daily_limit: number
  near_limit: boolean
}

// ---------- 用户体系 ----------
export type AccessPolicy = 'guest' | 'authenticated' | 'zhihu'

export interface Capabilities {
  usage_access_policy: AccessPolicy
  can_use: boolean
  required_action: 'none' | 'login' | 'zhihu'
  zhihu_oauth: { enabled: boolean; callback_path: string }
}

export interface UserInfo {
  id: string
  username: string | null
  account_kind: 'member' | 'zhihu'
  has_zhihu_oauth: boolean
  nickname: string
  avatar: string
  role: 'user' | 'admin'
  created_at: string | null
  last_login_at: string | null
}

export interface UserOverview {
  total_cases: number
  done_cases: number
  running_cases: number
  failed_cases: number
  questions: number
  favorites: number
}

export interface VerdictCard {
  case_id: string
  title: string
  proposition: string
  conclusion_stance: string
  confidence: number
  created_at: string | null
  is_favorite: boolean
}

export interface UserQuestionCard {
  id: string
  case_id: string
  case_title: string
  target: string
  text: string
  response: string
  challenge_type: string
  created_at: string | null
}

export interface LearningProfile {
  topics: { case_id: string; title: string; proposition: string; created_at: string }[]
  challenge_types: { type: string; label: string; count: number }[]
  summary: string
}

// ---------- 管理后台 ----------
export interface AdminDashboard {
  users_total: number
  users_today: number
  cases_total: number
  cases_today: number
  cases_running: number
  cases_failed: number
  cases_done: number
  questions_today: number
  trend: { date: string; cases: number; failed: number }[]
  cache_hit_rate: number | null
  recent_errors: { id: string; title: string; error: string; stage: string }[]
}

export interface AdminUser {
  id: string
  username: string | null
  nickname: string | null
  avatar: string
  account_kind: 'member' | 'zhihu'
  role: 'user' | 'admin'
  status: 'active' | 'disabled'
  created_at: string | null
  last_login_at: string | null
  case_count: number
}

export interface AdminCase {
  id: string
  title: string
  proposition: string
  status: CaseStatus
  engine_mode: string
  is_demo: boolean
  owner: string
  sources: number
  evidence: number
  created_at: string | null
  duration_s: number | null
  error_message: string | null
}

export interface ProviderStatus {
  name: string
  key: string
  mode: 'REAL' | 'MOCK' | 'FALLBACK' | 'DISABLED' | 'NOT_USED'
  daily_limit: number | null
  status: 'HEALTHY' | 'DEGRADED' | 'BLOCKED' | 'MOCK' | 'DOWN' | null
  calls: number
  cache_hits: number
  failures: number
  hit_rate: number | null
}

export interface SystemSettingItem {
  key: string
  value: unknown
  default: unknown
  description: string
  is_flag: boolean
  updated_at: string | null
  updated_by: string
}

export interface AuditItem {
  id: number
  admin: string
  action: string
  target: string
  detail: string
  ip: string
  result: string
  created_at: string | null
}

export interface Paged<T> {
  total: number
  page: number
  size: number
  items: T[]
}

export interface IntegrityReport {
  status: 'PASS' | 'WARNING' | 'FAIL'
  checked_at: string
  issues: { level: string; kind: string; detail: string }[]
  issue_count: number
}
