<script setup lang="ts">
// 用户中心：Overview / 我的案件 / 判决书收藏 / 我的质询 / 学习档案 / 设置
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NEmpty, NTabs, NTabPane, useDialog, useMessage } from 'naive-ui'
import { api } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import type { LearningProfile, UserOverview, UserQuestionCard, VerdictCard } from '@/types'
import { challengeTypeLabels, conclusionStanceLabels, fmtTime, pct } from '@/utils/display'
import { hasZhihuIdentity, safeRedirect, saveOAuthIntent } from '@/utils/access'
import StatusTag from '@/components/common/StatusTag.vue'
import CourtClerk from '@/components/common/CourtClerk.vue'
import AppLinkButton from '@/components/common/AppLinkButton.vue'

const AVATARS = ['⚖', '🎓', '🔬', '📚', '🧭', '🔭', '🧠', '🌱']
const route = useRoute()
const $router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const dialog = useDialog()

const tab = ref(typeof route.query.tab === 'string' ? route.query.tab : 'overview')
const overview = ref<UserOverview | null>(null)
const verdicts = ref<VerdictCard[]>([])
const favOnly = ref(false)
const learning = ref<LearningProfile | null>(null)
const myCaseCases = ref<{ id: string; title: string; status: string; is_public: boolean; created_at: string }[]>([])
const myQuestions = ref<UserQuestionCard[]>([])

// 每个 Tab 独立的加载/错误状态：接口失败不再被误显示为「没有数据」
const tabLoading = ref<Record<string, boolean>>({})
const tabError = ref<Record<string, string>>({})
const loadedTabs = ref<Set<string>>(new Set())

// 设置
const nickname = ref('')
const avatar = ref('')
const oldPassword = ref('')
const newPassword = ref('')
const savingProfile = ref(false)
const savingPassword = ref(false)
const bindPassword = ref('')
const bindPassword2 = ref('')
const bindingZhihu = ref(false)
const zhihuBound = computed(() => hasZhihuIdentity(auth.user))
const zhihuEnabled = computed(() => auth.capabilities?.zhihu_oauth.enabled === true)

const maxType = computed(() => Math.max(1, ...((learning.value?.challenge_types ?? []).map((t) => t.count) ?? [1])))

async function loadTab(name: string) {
  if (name === 'settings') {
    nickname.value = auth.user?.nickname ?? ''
    avatar.value = auth.user?.avatar ?? '⚖'
    return
  }
  if (loadedTabs.value.has(name)) return
  tabLoading.value = { ...tabLoading.value, [name]: true }
  tabError.value = { ...tabError.value, [name]: '' }
  try {
    if (name === 'overview') overview.value = await api.overview()
    else if (name === 'cases') {
      const list = await api.myCases()
      myCaseCases.value = list.map((c) => ({ id: c.id, title: c.title, status: c.status, is_public: c.is_public, created_at: c.created_at ?? '' }))
    } else if (name === 'favorites') verdicts.value = await api.myVerdicts(favOnly.value ? 'favorites' : 'recent')
    else if (name === 'questions') myQuestions.value = await api.myQuestions()
    else if (name === 'learning') learning.value = await api.learning()
    loadedTabs.value = new Set(loadedTabs.value).add(name)
  } catch (e) {
    tabError.value = { ...tabError.value, [name]: e instanceof Error ? e.message : '加载失败' }
  } finally {
    tabLoading.value = { ...tabLoading.value, [name]: false }
  }
}

function retryTab(name: string) {
  loadedTabs.value = new Set([...loadedTabs.value].filter((t) => t !== name))
  void loadTab(name)
}

watch(tab, (value) => {
  void $router.replace({ query: { ...route.query, tab: value } })
  void loadTab(value)
})

watch(
  () => route.query.tab,
  (value) => {
    if (typeof value === 'string' && value !== tab.value) {
      tab.value = value
      void loadTab(value)
    }
  },
)

async function saveProfile() {
  if (savingProfile.value) return
  savingProfile.value = true
  try {
    const res = await api.updateProfile({ nickname: nickname.value, avatar: avatar.value })
    auth.setUser(res.user)
    message.success('资料已更新')
  } catch (e) {
    message.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    savingProfile.value = false
  }
}

async function bindZhihu() {
  if (bindingZhihu.value) return
  if (!bindPassword.value || bindPassword.value !== bindPassword2.value) {
    message.warning(!bindPassword.value ? '请输入当前密码并确认' : '两次输入的密码不一致')
    return
  }
  dialog.warning({
    title: '确认绑定知乎账号',
    content: '绑定后该知乎账号将作为当前本站账号的登录身份。为保护案件归属，绑定后不能在此自行换绑。',
    positiveText: '确认并前往知乎',
    negativeText: '取消',
    onPositiveClick: async () => {
      bindingZhihu.value = true
      try {
        const redirect = safeRedirect(route.query.redirect, '/profile?tab=settings')
        saveOAuthIntent({ mode: 'link', redirect })
        const { authorize_url } = await api.zhihuLinkAuthorize(bindPassword.value)
        // 密码只提交给本站后端做即时校验，不写入 URL 或浏览器存储。
        bindPassword.value = bindPassword2.value = ''
        window.location.href = authorize_url
      } catch (e) {
        sessionStorage.removeItem('zhicourt_oauth_intent')
        message.error(e instanceof Error ? e.message : '无法发起知乎绑定')
        bindingZhihu.value = false
      }
    },
  })
}

async function savePassword() {
  if (savingPassword.value) return
  if (newPassword.value.length < 8) {
    message.warning('新密码至少 8 位')
    return
  }
  savingPassword.value = true
  try {
    await api.changePassword(oldPassword.value, newPassword.value)
    oldPassword.value = newPassword.value = ''
    message.success('密码已修改')
  } catch (e) {
    message.error(e instanceof Error ? e.message : '修改失败')
  } finally {
    savingPassword.value = false
  }
}

const favBusy = ref<string | null>(null)
const favError = ref('')
async function toggleFav(v: VerdictCard) {
  if (favBusy.value) return
  favBusy.value = v.case_id
  favError.value = ''
  const before = v.is_favorite
  try {
    const res = before ? await api.unfavorite(v.case_id) : await api.favorite(v.case_id)
    v.is_favorite = res.is_favorite
    if (overview.value) overview.value.favorites += res.is_favorite ? 1 : -1
    message.success(res.is_favorite ? '已收藏判决书' : '已取消收藏')
  } catch (e) {
    v.is_favorite = before // 失败回滚，本地状态与服务器保持一致
    favError.value = e instanceof Error ? e.message : '收藏失败'
    message.error(favError.value)
  } finally {
    favBusy.value = null
  }
}

async function loadFavorites() {
  loadedTabs.value = new Set([...loadedTabs.value].filter((t) => t !== 'favorites'))
  verdicts.value = []
  await loadTab('favorites')
}

onMounted(async () => {
  await Promise.all([loadTab(tab.value), auth.fetchCapabilities()])
})
</script>

<template>
  <main class="page">
    <div class="uc-head card card-pad">
      <span class="uc-avatar">{{ auth.user?.avatar }}</span>
      <div class="uc-info">
        <h1>{{ auth.user?.nickname }}</h1>
        <p>{{ auth.user?.username ? `@${auth.user.username}` : '知乎账号' }} · 加入于 {{ fmtTime(auth.user?.created_at ?? '') }}<template v-if="auth.user?.last_login_at"> · 最近登录 {{ fmtTime(auth.user.last_login_at) }}</template></p>
      </div>
      <AppLinkButton to="/case/new" type="primary" style="margin-left: auto">发起审理</AppLinkButton>
    </div>

    <NTabs v-model:value="tab" type="line" animated style="margin-top: 6px" @update:value="loadTab">
      <!-- Overview -->
      <NTabPane name="overview" tab="概览">
        <div v-if="tabLoading['overview']" class="skeleton-block" style="height: 120px" />
        <div v-else-if="tabError['overview']" class="error-state card">
          <p>{{ tabError['overview'] }}</p>
          <NButton size="small" @click="retryTab('overview')">重试</NButton>
        </div>
        <div v-else-if="overview" class="ov-grid">
          <RouterLink to="/cases" class="ov-card card"><b>{{ overview.total_cases }}</b><span>全部案件</span></RouterLink>
          <RouterLink to="/cases?status=verdict_ready" class="ov-card card"><b>{{ overview.done_cases }}</b><span>已宣判</span></RouterLink>
          <RouterLink to="/cases" class="ov-card card"><b>{{ overview.running_cases }}</b><span>进行中</span></RouterLink>
          <div class="ov-card card"><b>{{ overview.questions }}</b><span>提出质询</span></div>
          <div class="ov-card card ov-clickable" @click="tab = 'favorites'; loadTab('favorites')"><b>{{ overview.favorites }}</b><span>收藏判决</span></div>
          <div class="ov-card card"><b>{{ overview.failed_cases }}</b><span>失败案件</span></div>
        </div>
        <p v-if="overview && overview.total_cases === 0" class="empty-state">还没有案件，<RouterLink to="/case/new">发起第一次审理 →</RouterLink></p>
      </NTabPane>

      <!-- 我的案件 -->
      <NTabPane name="cases" tab="我的案件">
        <div v-if="tabLoading['cases']" class="skeleton-block" style="height: 160px" />
        <div v-else-if="tabError['cases']" class="error-state card">
          <p>{{ tabError['cases'] }}</p>
          <NButton size="small" @click="retryTab('cases')">重试</NButton>
        </div>
        <div v-else-if="myCaseCases.length" class="mc-list">
          <RouterLink v-for="c in myCaseCases" :key="c.id" :to="`/case/${c.id}`" class="mc-item card">
            <span class="mc-title" :title="c.title">{{ c.title }}</span>
            <span class="mc-visibility">{{ c.is_public ? '公开' : '私有' }}</span>
            <span style="margin-left: auto"><StatusTag :status="c.status as any" /></span>
            <span class="mc-time">{{ fmtTime(c.created_at) }}</span>
          </RouterLink>
        </div>
        <NEmpty v-else description="还没有自己的案件" style="padding: 48px">
          <AppLinkButton to="/case/new" type="primary" size="small">发起审理</AppLinkButton>
        </NEmpty>
      </NTabPane>

      <!-- 判决书收藏 -->
      <NTabPane name="favorites" tab="判决书与收藏">
        <p v-if="favError" class="fav-error" role="alert">{{ favError }}</p>
        <div class="fav-bar">
          <NButton size="small" :type="favOnly ? 'default' : 'primary'" @click="favOnly = false; loadFavorites()">最近判决</NButton>
          <NButton size="small" :type="favOnly ? 'primary' : 'default'" @click="favOnly = true; loadFavorites()">⭐ 已收藏</NButton>
        </div>
        <div v-if="tabLoading['favorites']" class="skeleton-block" style="height: 200px" />
        <div v-else-if="tabError['favorites']" class="error-state card">
          <p>{{ tabError['favorites'] }}</p>
          <NButton size="small" @click="retryTab('favorites')">重试</NButton>
        </div>
        <div v-else-if="verdicts.length" class="vd-list">
          <div v-for="v in verdicts" :key="v.case_id" class="vd-item card">
            <RouterLink :to="`/case/${v.case_id}/verdict`" class="vd-title" :title="v.title">{{ v.title }}</RouterLink>
            <div class="vd-prop">{{ v.proposition }}</div>
            <div class="vd-meta">
              <span class="side-chip court">{{ conclusionStanceLabels[v.conclusion_stance] ?? v.conclusion_stance }}</span>
              <span class="vd-conf">置信度 {{ pct(v.confidence) }}</span>
              <span class="vd-time">{{ fmtTime(v.created_at ?? '') }}</span>
              <button type="button" class="fav-btn" :class="{ on: v.is_favorite }" :disabled="favBusy !== null" :aria-busy="favBusy === v.case_id" @click="toggleFav(v)">
                {{ v.is_favorite ? '⭐ 已收藏' : '☆ 收藏' }}
              </button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">
          <CourtClerk variant="sleepy" :size="100" caption="还没有判决书——先去完成一次审理吧" />
        </div>
      </NTabPane>

      <!-- 我的质询 -->
      <NTabPane name="questions" tab="我的质询">
        <div v-if="tabLoading['questions']" class="skeleton-block" style="height: 160px" />
        <div v-else-if="tabError['questions']" class="error-state card">
          <p>{{ tabError['questions'] }}</p>
          <NButton size="small" @click="retryTab('questions')">重试</NButton>
        </div>
        <div v-else-if="myQuestions.length" class="q-list">
          <div v-for="q in myQuestions" :key="q.id" class="q-item card">
            <div class="q-top">
              <span class="ev-type" style="background: var(--c-bg)">{{ challengeTypeLabels[q.challenge_type] ?? q.challenge_type }}</span>
              <RouterLink :to="`/case/${q.case_id}`" class="q-case" :title="q.case_title">{{ q.case_title }}</RouterLink>
              <span class="q-time">{{ fmtTime(q.created_at ?? '') }}</span>
            </div>
            <div class="q-text">{{ q.text }}</div>
          </div>
        </div>
        <NEmpty v-else description="还没有质询。在庭审完成后点击「我来质询」参与审理。" style="padding: 48px" />
      </NTabPane>

      <!-- 学习档案 -->
      <NTabPane name="learning" tab="学习档案">
        <div v-if="tabLoading['learning']" class="skeleton-block" style="height: 160px" />
        <div v-else-if="tabError['learning']" class="error-state card">
          <p>{{ tabError['learning'] }}</p>
          <NButton size="small" @click="retryTab('learning')">重试</NButton>
        </div>
        <template v-else-if="learning">
          <div class="lp-summary card card-pad">{{ learning.summary }}</div>
          <div v-if="learning.challenge_types.length" class="lp-grid">
            <div class="card card-pad lp-block">
              <h3>质询类型分布</h3>
              <div v-for="t in learning.challenge_types" :key="t.type" class="lp-row">
                <span class="lp-label">{{ t.label }}</span>
                <div class="score-bar" style="flex: 1"><div class="fill" :style="{ width: `${(t.count / maxType) * 100}%` }" /></div>
                <span class="lp-count">{{ t.count }}</span>
              </div>
            </div>
            <div class="card card-pad lp-block">
              <h3>审理过的主题</h3>
              <RouterLink v-for="t in learning.topics" :key="t.case_id" :to="`/case/${t.case_id}/verdict`" class="lp-topic" :title="t.title">
                {{ t.title }}
              </RouterLink>
              <p v-if="!learning.topics.length" class="hint-text">完成审理后这里会记录你研究过的主题。</p>
            </div>
          </div>
          <div v-else class="empty-state card" style="margin-top: 16px">
            <div class="icon">📈</div>
            <p>质询与审理数据会以客观行为统计呈现在这里（不做主观能力评分）。</p>
          </div>
        </template>
      </NTabPane>

      <!-- 设置 -->
      <NTabPane name="settings" tab="设置">
        <div class="set-grid">
          <div class="card card-pad">
            <h3>个人资料</h3>
            <label class="field"><span>昵称</span>
              <input v-model="nickname" class="text-input" maxlength="30" />
            </label>
            <label class="field"><span>头像</span>
              <div class="avatar-row">
                <button v-for="a in AVATARS" :key="a" class="avatar-pick" :class="{ on: avatar === a }" @click="avatar = a">{{ a }}</button>
              </div>
            </label>
            <NButton type="primary" :loading="savingProfile" @click="saveProfile">保存资料</NButton>
          </div>
          <div v-if="auth.user?.account_kind === 'member'" class="card card-pad">
            <h3>修改密码</h3>
            <label class="field"><span>原密码</span>
              <input v-model="oldPassword" class="text-input" type="password" autocomplete="current-password" />
            </label>
            <label class="field"><span>新密码（至少 8 位）</span>
              <input v-model="newPassword" class="text-input" type="password" autocomplete="new-password" />
            </label>
            <NButton :loading="savingPassword" @click="savePassword">修改密码</NButton>
            <p class="hint-text" style="margin-top: 12px">
              隐私说明：你的案件默认私有，只有你主动公开后才会出现在公开列表或分享链接中；质询与学习档案仅自己可见。管理员访问你的私人数据会留下审计记录。
            </p>
          </div>
          <div v-if="auth.user?.account_kind === 'member'" class="card card-pad">
            <h3>绑定知乎账号</h3>
            <template v-if="zhihuBound">
              <p class="bind-ok">已绑定知乎账号</p>
              <p class="hint-text">为保护账号与案件归属，暂不支持自行换绑；如需处理请联系管理员。</p>
            </template>
            <template v-else-if="zhihuEnabled">
              <p class="hint-text">绑定后可在“仅知乎用户”策略下继续使用业务功能。若此前只登录过知乎且未产生案件或收藏，重新授权会把知乎身份转到当前注册账号。绑定不会把 OAuth Token 暴露给前端。</p>
              <label class="field"><span>当前密码</span>
                <input v-model="bindPassword" class="text-input" type="password" autocomplete="current-password" />
              </label>
              <label class="field"><span>确认当前密码</span>
                <input v-model="bindPassword2" class="text-input" type="password" autocomplete="current-password" />
              </label>
              <NButton :loading="bindingZhihu" :disabled="bindingZhihu" @click="bindZhihu">验证密码并绑定知乎</NButton>
            </template>
            <p v-else class="hint-text">知乎 OAuth 尚未配置，暂时无法绑定。</p>
          </div>
          <div v-else class="card card-pad">
            <h3>知乎账号登录</h3>
            <p class="bind-ok">当前账号已通过知乎授权</p>
            <p class="hint-text">本站不保存知乎密码，也不会向浏览器暴露 OAuth Token。为保护案件归属，不支持换绑。</p>
          </div>
        </div>
      </NTabPane>
    </NTabs>
  </main>
</template>

<style scoped>
.uc-head {
  display: flex;
  align-items: center;
  gap: 18px;
}
.uc-avatar {
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: var(--c-primary-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
}
.uc-info h1 {
  font-size: 21px;
}
.uc-info p {
  color: var(--c-ink-3);
  font-size: 13px;
  margin: 4px 0 0;
}
.ov-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-top: 8px;
}
.ov-card {
  padding: 18px 10px;
  text-align: center;
  color: var(--c-ink);
  display: block;
}
.ov-card b {
  display: block;
  font-size: 26px;
  font-weight: 700;
}
.ov-card span {
  font-size: 12.5px;
  color: var(--c-ink-3);
}
.ov-clickable {
  cursor: pointer;
}
.ov-clickable:hover {
  border-color: var(--c-primary);
}

.mc-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mc-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 18px;
  color: var(--c-ink);
}
.mc-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-time {
  font-size: 12px;
  color: var(--c-ink-3);
}
.mc-visibility {
  font-size: 11.5px;
  padding: 1px 7px;
  border-radius: 4px;
  background: var(--c-bg);
  color: var(--c-ink-3);
}

.fav-error { color: var(--c-danger); font-size: 13px; margin: 0 0 10px; }
.fav-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.vd-list,
.q-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.vd-item {
  padding: 16px 20px;
}
.vd-title {
  font-weight: 600;
  font-size: 15px;
}
.vd-prop {
  color: var(--c-ink-3);
  font-size: 13px;
  margin: 4px 0 10px;
}
.vd-meta {
  display: flex;
  align-items: center;
  gap: 14px;
}
.vd-conf,
.vd-time,
.q-time {
  font-size: 12.5px;
  color: var(--c-ink-3);
}
.fav-btn {
  margin-left: auto;
  border: 1px solid var(--c-line);
  background: #fff;
  border-radius: 6px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 12.5px;
  font-family: var(--font);
  color: var(--c-ink-2);
}
.fav-btn.on {
  background: #fdf3e0;
  border-color: #efd9ac;
  color: #92651a;
}
.q-item {
  padding: 13px 18px;
}
.q-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.q-case {
  font-size: 12.5px;
}
.q-time {
  margin-left: auto;
}
.q-text {
  font-size: 13.5px;
  color: var(--c-ink-2);
}

.lp-summary {
  font-size: 14.5px;
  margin-top: 8px;
}
.lp-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
.lp-block h3 {
  font-size: 14.5px;
  margin-bottom: 14px;
}
.lp-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.lp-label {
  width: 80px;
  font-size: 13px;
  color: var(--c-ink-2);
}
.lp-count {
  font-family: var(--mono);
  font-size: 12.5px;
  width: 24px;
  text-align: right;
}
.lp-topic {
  display: block;
  padding: 7px 0;
  border-bottom: 1px dashed var(--c-line);
  font-size: 13.5px;
}

.set-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 8px;
}
.set-grid h3 {
  font-size: 15px;
  margin-bottom: 14px;
}
.avatar-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.avatar-pick {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid var(--c-line);
  background: var(--c-bg);
  font-size: 19px;
  cursor: pointer;
}
.avatar-pick.on {
  border-color: var(--c-primary);
  background: var(--c-primary-bg);
}

@media (max-width: 900px) {
  .ov-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .lp-grid,
  .set-grid {
    grid-template-columns: 1fr;
  }
}
</style>
