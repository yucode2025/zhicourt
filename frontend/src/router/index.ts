import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { isAccessExemptRoute, policyAllows } from '@/utils/access'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/case/new', name: 'case-new', component: () => import('@/views/CaseNewView.vue'), meta: { business: true } },
    { path: '/case/:id', name: 'court', component: () => import('@/views/CourtView.vue'), meta: { business: true } },
    { path: '/case/:id/evidence', name: 'evidence-graph', component: () => import('@/views/EvidenceGraphView.vue'), meta: { business: true } },
    { path: '/case/:id/verdict', name: 'verdict', component: () => import('@/views/VerdictView.vue'), meta: { business: true } },
    { path: '/cases', name: 'cases', component: () => import('@/views/CasesView.vue'), meta: { business: true } },
    // 公开分享（只读）
    { path: '/share/:publicId', name: 'share', component: () => import('@/views/ShareView.vue'), meta: { accessExempt: true } },
    // 认证
    { path: '/login', name: 'login', component: () => import('@/views/auth/LoginView.vue'), meta: { accessExempt: true } },
    { path: '/login/zhihu', name: 'zhihu-callback', component: () => import('@/views/auth/ZhihuCallbackView.vue'), meta: { accessExempt: true } },
    { path: '/register', name: 'register', component: () => import('@/views/auth/RegisterView.vue'), meta: { accessExempt: true } },
    // 用户中心：即使业务策略要求知乎，普通账号也必须能进入以完成绑定。
    { path: '/profile', name: 'profile', component: () => import('@/views/user/ProfileView.vue'), meta: { requiresAuth: true, accessExempt: true } },
    // 管理后台（后端强制 ADMIN；此处 guard 仅改善体验）
    {
      path: '/admin',
      component: () => import('@/components/admin/AdminLayout.vue'),
      meta: { requiresAdmin: true, accessExempt: true },
      children: [
        { path: '', name: 'admin-dashboard', component: () => import('@/views/admin/AdminDashboard.vue') },
        { path: 'users', name: 'admin-users', component: () => import('@/views/admin/AdminUsers.vue') },
        { path: 'cases', name: 'admin-cases', component: () => import('@/views/admin/AdminCases.vue') },
        { path: 'api-usage', name: 'admin-api-usage', component: () => import('@/views/admin/AdminApiUsage.vue') },
        { path: 'providers', name: 'admin-providers', component: () => import('@/views/admin/AdminProviders.vue') },
        { path: 'audit', name: 'admin-audit', component: () => import('@/views/admin/AdminAudit.vue') },
        { path: 'settings', name: 'admin-settings', component: () => import('@/views/admin/AdminSettings.vue') },
      ],
    },
    // 错误页
    { path: '/403', name: 'forbidden', component: () => import('@/views/ForbiddenView.vue') },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  let user = auth.user
  if (to.meta.requiresAuth || to.meta.requiresAdmin) {
    user = await auth.fetchMe()
    if (!user) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.requiresAdmin && user.role !== 'admin') return { name: 'forbidden' }
  }

  if (to.meta.business && !isAccessExemptRoute(to)) {
    const [nextUser, capabilities] = await Promise.all([auth.fetchMe(), auth.fetchCapabilities()])
    user = nextUser
    // 能力接口失败时不猜测宽松策略，明确导向登录页展示错误并允许重试。
    if (!capabilities) {
      return { name: 'login', query: { redirect: to.fullPath, capability_error: '1' } }
    }
    if (!capabilities.can_use || capabilities.required_action !== 'none' || !policyAllows(capabilities.usage_access_policy, user)) {
      const needsZhihu = capabilities.required_action === 'zhihu' || capabilities.usage_access_policy === 'zhihu'
      if (needsZhihu && user) {
        return { name: 'profile', query: { tab: 'settings', redirect: to.fullPath } }
      }
      return {
        name: 'login',
        query: {
          redirect: to.fullPath,
          required: needsZhihu ? 'zhihu' : 'authenticated',
        },
      }
    }
  }
  return true
})

export default router
