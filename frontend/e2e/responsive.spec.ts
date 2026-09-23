import { expect, test } from '@playwright/test'

const verdictCase = {
  id: 'e2e-verdict',
  title: '人工智能是否会显著改变初级软件岗位',
  proposition: '人工智能是否会显著改变初级软件岗位',
  original_question: '人工智能会淘汰程序员吗？',
  status: 'verdict_ready',
  current_stage: 'done',
  engine_mode: 'heuristic',
  // 模拟滚动发布期间的旧响应：execution_summary 字段尚不存在。
  execution_summary: undefined,
  is_demo: true,
  is_public: false,
  public_id: null,
  progress_index: 18,
  created_at: '2026-09-13T00:00:00',
  error_message: null,
  plan: null,
  progress_events: [],
  is_owner: false,
  sources: [],
  evidence: [],
  claims: [],
  arguments: [],
  agent_runs: [],
  cross_examinations: [],
  user_questions: [],
  verdict: {
    id: 'vd-e2e',
    conclusion: '在重复性任务上影响显著，但岗位变化取决于组织采用速度、能力边界和人机协作方式。',
    conclusion_stance: 'conditional',
    confidence: 0.67,
    prosecution_summary: '自动化会压缩部分重复性编码工作。',
    defense_summary: '需求理解、系统权衡和责任承担仍需要人类。',
    shared_facts: ['工具能力正在快速提高。'],
    core_disputes: ['生产率提升是否等同于岗位净减少。'],
    strongest_evidence_ids: [],
    strongest_counter_evidence_ids: [],
    evidence_gaps: ['缺少跨行业长期追踪数据。'],
    definition_conflicts: ['“淘汰”是岗位消失还是任务结构变化。'],
    unknowns: ['技术扩散速度仍不确定。'],
    verdict_changers: ['新的长期就业统计可能改变当前判断。'],
    next_questions: ['如何衡量人机协作后的实际生产率？'],
    cross_exam_summary: '现有证据支持条件性结论，不能外推为所有岗位都会消失。',
  },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"guest"}' }))
  await page.route('**/api/capabilities', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ usage_access_policy: 'guest', can_use: true, required_action: 'none', zhihu_oauth: { enabled: false, callback_path: '/login/zhihu' } }),
  }))
  await page.route('**/api/hot', (route) => route.fulfill({ contentType: 'application/json', body: '{"items":[],"is_demo":false,"error":"offline"}' }))
  await page.route('**/api/hackathon/content**', (route) => {
    const kind = new URL(route.request().url()).searchParams.get('kind') === 'story' ? 'story' : 'knowledge'
    const title = kind === 'story' ? '测试故事' : '测试知识'
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        kind,
        items: [{ work_id: kind === 'story' ? '2' : '1', title, description: '来自官方活动接口的选题摘要', labels: ['测试'], artwork: '', tab_artwork: '' }],
      }),
    })
  })
  await page.goto('/')
})

test('header exposes exactly one responsive navigation', async ({ page }, testInfo) => {
  const mobile = testInfo.project.name === 'mobile-390'
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(page.locator('.mobile-actions')).toBeVisible({ visible: mobile })
  await expect(page.locator('.desktop-actions')).toBeVisible({ visible: !mobile })
  await expect(page.locator('header a button, header button a')).toHaveCount(0)

  if (mobile) {
    const menu = page.getByRole('button', { name: '主导航菜单' })
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    await menu.click()
    await expect(menu).toHaveAttribute('aria-expanded', 'true')
    for (const item of ['首页', '历史案件', '发起审理', '登录', '注册']) {
      await expect(page.getByRole('menuitem', { name: item, exact: true })).toBeVisible()
    }
  } else {
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    await expect(page.getByRole('link', { name: '历史案件' })).toBeVisible()
  }
})

test('login is a native form', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('form')).toHaveCount(1)
  await expect(page.locator('button[type="submit"]')).toHaveCount(1)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('hackathon knowledge and story items can start a review topic', async ({ page }) => {
  await expect(page.getByRole('heading', { name: '测试知识' })).toBeVisible()
  await page.getByRole('tab', { name: '知乎故事' }).click()
  await expect(page.getByRole('heading', { name: '测试故事' })).toBeVisible()
  await page.getByRole('button', { name: '以“测试故事”为线索发起审理' }).click()
  await expect(page).toHaveURL(/\/case\/new\?q=/)
})

test('configured Zhihu OAuth completes and restores the original destination', async ({ page }) => {
  await page.route('**/api/capabilities', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ usage_access_policy: 'guest', can_use: true, required_action: 'none', zhihu_oauth: { enabled: true, callback_path: '/login/zhihu' } }),
  }))
  await page.route('**/api/auth/zhihu/authorize', (route) => route.fulfill({
    contentType: 'application/json',
    body: '{"authorize_url":"http://127.0.0.1:4173/login/zhihu?authorization_code=e2e-code&state=e2e-state"}',
  }))
  await page.route('**/api/auth/zhihu/callback', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      user: {
        id: 'usr_e2e', username: null, account_kind: 'zhihu', nickname: '知乎知友',
        avatar: '⚖', role: 'user', created_at: null, last_login_at: null,
      },
      created: true,
      linked: false,
      migrated_cases: 0,
    }),
  }))
  await page.route('**/api/cases**', (route) => route.fulfill({ contentType: 'application/json', body: '[]' }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ user: { id: 'usr_e2e', username: null, account_kind: 'zhihu', nickname: '知乎知友', avatar: '⚖', role: 'user', created_at: null, last_login_at: null } }) }))

  await page.goto('/login?redirect=/cases')
  await page.getByRole('button', { name: '使用知乎登录' }).click()

  await expect(page).toHaveURL(/\/cases$/)
})

test('verdict remains readable without horizontal overflow', async ({ page }) => {
  await page.route('**/api/cases/e2e-verdict/favorite', (route) => route.fulfill({
    contentType: 'application/json', body: '{"is_favorite":false}',
  }))
  await page.route('**/api/cases/e2e-verdict', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify(verdictCase),
  }))
  await page.goto('/case/e2e-verdict/verdict')

  await expect(page.getByRole('heading', { name: '知识判决书' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

const longTitle = '远程办公对员工福祉、组织协作、城市结构与长期生产率的综合影响是否足以支持企业把它作为默认工作方式，并应如何设置公平且可核验的边界条件？'
const hardeningCase = {
  ...verdictCase,
  id: 'e2e-hardening',
  title: longTitle,
  original_question: longTitle,
  proposition: '企业是否应在明确边界下把远程办公作为默认工作方式',
  is_owner: true,
  is_public: true,
  public_id: 'public-hardening',
  sources: [{
    id: 'src-hardening', origin: 'web', kind: 'report', title: '远程办公长期跟踪报告',
    url: 'https://example.com/report', author: '研究机构', summary: '包含生产率与员工福祉数据。',
    published_at: '2026-01-01', vote_count: 0, comment_count: 0, rank_score: 0.8,
    independence_score: 1, is_demo: false,
  }],
  evidence: [{
    id: 'ev-neutral', source_id: 'src-hardening', claim: '混合办公结果受组织流程影响', stance: 'neutral',
    evidence_type: 'data', summary: '长期跟踪显示不同组织之间差异显著。', quoted_fragment: null,
    strength: 0.78, limitations: [],
  }],
  claims: [{ id: 'claim-pro', side: 'pro', text: '清晰流程可改善结果', evidence_ids: ['ev-neutral'] }],
  arguments: [
    { id: 'arg-pro', side: 'prosecution', title: '支持默认远程', body: '在流程清晰时可提高自主性。', evidence_ids: ['ev-neutral'], claim_ids: ['claim-pro'], strength: 0.75 },
    { id: 'arg-def', side: 'defense', title: '反对一刀切', body: '不同岗位条件差异较大。', evidence_ids: [], claim_ids: [], strength: 0.7 },
  ],
}

function challengeResponse(target: string, targetRefId: string | null) {
  return {
    id: `uq-${target}-${targetRefId ?? 'side'}`, target, target_ref_id: targetRefId,
    text: '请核验这项判断依据', challenge_type: 'evidence', response: '已按焦点证据核验。',
    related_evidence_ids: targetRefId === 'arg-def' ? [] : ['ev-neutral'], created_at: '2026-09-13T00:00:00',
  }
}

test('challenge controls send exact refs while shared verdict stays read-only', async ({ page }, testInfo) => {
  const requests: { target: string; target_ref_id: string | null }[] = []
  await page.route('**/api/cases/e2e-hardening**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/questions') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { target: string; target_ref_id: string | null }
      requests.push(body)
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(challengeResponse(body.target, body.target_ref_id)) })
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(hardeningCase) })
  })
  await page.goto('/case/e2e-hardening')
  await expect(page.getByRole('heading', { name: longTitle })).toHaveAttribute('title', longTitle)

  const submitChallenge = async () => {
    await page.getByRole('textbox', { name: '质询内容（至少 4 个字）' }).fill('请核验这项判断依据')
    await page.getByRole('button', { name: '提交质询' }).click()
    await expect(page.getByText('已按焦点证据核验。')).toBeVisible()
    await page.getByRole('button', { name: '知道了' }).click()
  }

  await page.getByRole('button', { name: '质询这条论证' }).first().click()
  await submitChallenge()
  await page.getByRole('button', { name: '我来质询' }).first().click()
  await submitChallenge()
  await page.getByText('来源（1）', { exact: true }).click()
  await page.getByRole('button', { name: '质询此来源' }).click()
  await submitChallenge()
  await page.getByText('证据（1）', { exact: true }).click()
  await page.getByRole('button', { name: '查看完整证据与来源 →' }).click()
  await page.getByRole('button', { name: '质疑这条证据' }).click()
  await submitChallenge()
  await page.getByRole('button', { name: '质询 Judge' }).click()
  await submitChallenge()

  expect(requests).toEqual([
    expect.objectContaining({ target: 'prosecution', target_ref_id: 'arg-pro' }),
    expect.objectContaining({ target: 'prosecution', target_ref_id: null }),
    expect.objectContaining({ target: 'source', target_ref_id: 'src-hardening' }),
    expect.objectContaining({ target: 'evidence', target_ref_id: 'ev-neutral' }),
    expect.objectContaining({ target: 'judge', target_ref_id: 'vd-e2e' }),
  ])
  await page.screenshot({ path: testInfo.outputPath('challenge-refs.png'), fullPage: true })

  await page.route('**/api/share/public-hardening', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...hardeningCase, created_at: hardeningCase.created_at }),
  }))
  await page.goto('/share/public-hardening')
  await expect(page.getByRole('heading', { name: longTitle })).toHaveAttribute('title', longTitle)
  await expect(page.getByRole('button', { name: '我来质询' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '质询这条论证' })).toHaveCount(0)
  await expect(page.getByText('公开只读')).toHaveCount(2)
  await page.screenshot({ path: testInfo.outputPath('share-readonly.png'), fullPage: true })
})

test('graph deep links gate lifecycle states and expose isolated ready evidence', async ({ page }, testInfo) => {
  await page.route('**/api/cases/e2e-hardening', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...hardeningCase, status: 'created' }),
  }))
  await page.goto('/case/e2e-hardening/evidence')
  await expect(page.getByRole('heading', { name: '图谱暂不可用' })).toBeVisible()
  await expect(page.getByText('案件尚未开始审理，暂无证据图谱。')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('graph-created-gate.png'), fullPage: true })

  await page.unroute('**/api/cases/e2e-hardening')
  await page.route('**/api/cases/e2e-hardening', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...hardeningCase, arguments: hardeningCase.arguments.map((item) => ({ ...item, evidence_ids: [] })) }),
  }))
  await page.goto('/case/e2e-hardening/evidence')
  if (testInfo.project.name === 'mobile-390') {
    await expect(page.getByText(/暂无已引用的核心链路；以下孤立证据和来源/)).toBeVisible()
    await page.getByRole('button', { name: '手动画布' }).click()
  } else {
    await expect(page.getByText('暂无已引用的核心链路。切换到“全部与孤立项”查看未引用的证据和来源。')).toBeVisible()
  }
  await page.getByRole('button', { name: '全部与孤立项' }).click()
  await expect(page.getByRole('button', { name: /查看节点详情：混合办公结果受组织流程影响/ })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('graph-isolated-ready.png'), fullPage: true })
})

test('plan result scroll honors reduced motion', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.route('**/api/plan', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      title: longTitle, proposition: '远程办公是否值得长期推广', key_concepts: ['远程办公'],
      disputes: ['生产率与福祉'], sub_questions: ['长期影响'], search_queries: ['远程办公 长期 数据'],
      suitable: true, needs_rewrite: false, mode: 'heuristic', input_classification: 'debatable', rejection_reason: null,
    }),
  }))
  await page.goto('/case/new')
  await page.getByRole('textbox').fill(longTitle)
  await page.getByRole('button', { name: '生成审理命题' }).click()
  await expect(page.getByText('确认审理命题')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  await page.screenshot({ path: testInfo.outputPath('plan-reduced-motion.png'), fullPage: true })
})
