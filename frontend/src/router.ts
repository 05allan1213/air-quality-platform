import { createRouter, createWebHistory } from 'vue-router'
import { scopeQuery, useSessionStore } from './stores/session'

const workspaceActions: Record<string, string[]> = {
  'manage-collection': ['run_collection', 'manage_collection', 'configure_collection_schedule', 'import_history', 'review_observation'],
  'manage-quality': ['review_observation', 'adjudicate_quality', 'manage_catalog', 'import_history'],
  'manage-operations': [
    'diagnose_operations', 'manage_grants', 'manage_accounts', 'maintain_city_search_alias',
    'review_observation', 'configure_collection_schedule', 'manage_collection',
  ],
}

export function canEnter(name: unknown, actions: string[]) {
  const required = typeof name === 'string' ? workspaceActions[name] : undefined
  return !required || required.some(action => actions.includes(action))
}

export const router = createRouter({
  history: createWebHistory('/app/'),
  routes: [
    { path: '/login', name: 'login', component: () => import('./auth/LoginPage.vue'), meta: { public: true, title: '登录' } },
    { path: '/register', name: 'register', component: () => import('./auth/RegisterPage.vue'), meta: { public: true, title: '创建账号' } },
    { path: '/about', name: 'about', component: () => import('./views/AboutPage.vue'), meta: { public: true, title: '平台说明' } },
    { path: '/sources', name: 'sources', component: () => import('./views/SourcesPage.vue'), meta: { public: true, title: '数据来源' } },
    { path: '/', name: 'overview', component: () => import('./monitor/Overview.vue'), meta: { title: '监测总览' } },
    { path: '/cities/:id', name: 'city', component: () => import('./monitor/City.vue'), meta: { title: '城市详情' } },
    { path: '/stations/:id', name: 'station', component: () => import('./monitor/Station.vue'), meta: { title: '站点详情' } },
    { path: '/analysis', name: 'analysis', component: () => import('./research/Analysis.vue'), meta: { title: '分析工作台' } },
    { path: '/query', name: 'query', component: () => import('./research/Query.vue'), meta: { title: '数据查询' } },
    { path: '/me', name: 'me', component: () => import('./views/MySpace.vue'), meta: { title: '我的空间' } },
    { path: '/manage/collection', name: 'manage-collection', component: () => import('./manage/Collection.vue'), meta: { title: '采集任务', manage: true } },
    { path: '/manage/quality', name: 'manage-quality', component: () => import('./manage/Quality.vue'), meta: { title: '数据质量', manage: true } },
    { path: '/manage/operations', name: 'manage-operations', component: () => import('./manage/Operations.vue'), meta: { title: '系统运行', manage: true } },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFound.vue'), meta: { title: '页面不存在' } },
  ],
  scrollBehavior(to, from, saved) {
    if (saved) return saved
    return to.path === from.path ? undefined : { top: 0 }
  },
})

router.beforeEach(async (to, from) => {
  if (to.meta.public) return true
  const session = useSessionStore()
  if (session.logoutPending) return { name: 'login', replace: true }
  const fromPublic = from.meta.public && from.name !== 'login' && from.name !== 'register'
  const crossWorkspace = Boolean(from.matched.length && (fromPublic || Boolean(from.meta.manage) !== Boolean(to.meta.manage)))
  try {
    await session.ensureLoaded(crossWorkspace)
  } catch {
    if (session.authenticated && !from.meta.public) return false // The current shell keeps its retry state.
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (session.logoutPending) return { name: 'login', replace: true }
  if (!session.verified) return { name: 'login', query: { next: to.fullPath } }
  if (to.query.scope !== undefined && to.query.scope !== 'synthetic') {
    // A malformed domain must not reinterpret object IDs as formal-domain selections.
    return { name: 'overview', query: scopeQuery(session.scope), replace: true }
  }
  if (!canEnter(to.name, session.capabilities.actions)) {
    return { name: 'overview', query: scopeQuery(session.scope), replace: true }
  }
  if (session.scopeInitialized) {
    const requestedScope = to.query.scope === 'synthetic' ? 'synthetic' : ''
    if (to.name !== 'overview' && requestedScope !== session.scope) {
      // Object IDs are product-scoped; a domain switch starts again at the overview.
      return { name: 'overview', query: scopeQuery(session.scope), replace: true }
    }
  }
  return true
})

router.afterEach((to, _from, failure) => {
  if (failure) return
  document.title = `${String(to.meta.title ?? '空气质量平台')} · 城市空气质量监测数据分析平台`
  if (!to.meta.public) useSessionStore().enterScope(to.query.scope === 'synthetic' ? 'synthetic' : '')
})
