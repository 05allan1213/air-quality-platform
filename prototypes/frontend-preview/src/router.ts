import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'national', meta: { title: '全国空气质量总览' }, component: () => import('./national/NationalPage.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior(to, from, position) {
    if (position) return position
    if (to.path !== from.path) return { top: 0 }
  },
})
router.afterEach((to) => { document.title = `${to.meta.title} · 澄空观测（F 合成演示）` })
