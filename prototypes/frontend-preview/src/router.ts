import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'national', meta: { title: '全国空气质量总览' }, component: () => import('./national/NationalPage.vue') },
    { path: '/city', name: 'city', meta: { title: '城市详情' }, component: () => import('./city/CityPage.vue') },
    { path: '/analysis', name: 'analysis', meta: { title: '分析工作台' }, component: () => import('./analysis/AnalysisPage.vue') },
    { path: '/quality', name: 'quality', meta: { title: '质量事件详情' }, component: () => import('./quality/QualityPage.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/city' },
  ],
  scrollBehavior(to, from, position) {
    if (position) return position
    if (to.path !== from.path) return { top: 0 }
  },
})
router.afterEach((to) => { document.title = `${to.meta.title} · 澄空观测（F 合成演示）` })
