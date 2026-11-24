<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElConfigProvider, ElDialog, ElDrawer } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ArrowDown, ArrowRight, Check, Close, Fold, House, Lock, Operation, SetUp, Switch, User } from '@element-plus/icons-vue'
import { cityStates, isCityState, usePreviewStore } from './shared/preview'

const route = useRoute()
const router = useRouter()
const store = usePreviewStore()
const settings = ref(false)
const navigation = ref(false)
const scopeLabel = ref('')
const main = ref<HTMLElement>()
const admin = computed(() => route.name === 'quality')
const navItems = computed(() => admin.value ? [
  { text: '采集任务', path: '' }, { text: '数据质量', path: '/quality' }, { text: '系统运行', path: '' },
] : [
  { text: '监测总览', path: '/' }, { text: '分析工作台', path: '/analysis' },
  { text: '数据查询', path: '' }, { text: '我的空间', path: '' },
])
const context = computed(() => admin.value ? '数据质量' : route.name === 'city' ? '监测总览' : '')
function outOfScope(label: string) { navigation.value = false; scopeLabel.value = label }
function workspace() {
  navigation.value = false
  if (!admin.value && !store.canGovern) { settings.value = true; store.notify('管理工作区需要具名治理权限'); return }
  void router.push(admin.value ? '/city' : '/quality')
}
function setRole(value: 'operator' | 'reader') {
  store.role = value
  store.notify(value === 'reader' ? '已切换为观察账号' : '已切换为具备质量治理权限的账号')
}
function selectCityState(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (isCityState(value)) void router.replace({ query: { ...route.query, state: value } })
}
watch(() => route.query.state, (value) => { if (route.name === 'city') store.cityState = isCityState(value) ? value : 'normal' }, { immediate: true })
watch(() => route.name, async (_, previous) => {
  if (!previous) return
  navigation.value = false; scopeLabel.value = ''; settings.value = false
  await nextTick(); (main.value ?? document.getElementById('content'))?.focus({ preventScroll: true })
})
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <a class="skip-link" href="#content">跳到主要内容</a>
    <RouterView v-if="route.name === 'national'" @settings="settings = true" @workspace="workspace" @scope="outOfScope" />
    <header v-if="route.name !== 'national'" class="site-header">
      <div class="header-inner">
        <button class="icon-btn mobile-menu" aria-label="打开导航" :aria-expanded="navigation" @click="navigation = true"><Fold aria-hidden="true" /></button>
        <RouterLink to="/city" class="brand" aria-label="澄空观测，返回城市详情"><img src="/mark.svg" alt="" width="35" height="35" /><span>澄空观测</span></RouterLink>
        <nav class="desktop-nav" :aria-label="admin ? '管理工作区导航' : '观测工作区导航'">
          <template v-for="item in navItems" :key="item.text">
            <RouterLink v-if="item.path" :to="item.path" class="nav-item" active-class="current">{{ item.text }}</RouterLink>
            <button v-else class="nav-item" :class="{ current: context === item.text }" :title="`${item.text} · 原型范围外`" @click="outOfScope(item.text)">{{ item.text }}</button>
          </template>
        </nav>
        <div class="head-tools">
          <span class="demo-badge"><b>F</b> 合成演示</span>
          <span class="header-rule" />
          <button class="workspace-btn" @click="workspace"><Switch aria-hidden="true" />{{ admin ? '观测工作区' : '管理工作区' }}</button>
          <button class="account-btn" aria-label="演示账号与权限设置" @click="settings = true"><User aria-hidden="true" /></button>
        </div>
      </div>
    </header>
    <main v-if="route.name !== 'national'" id="content" ref="main" tabindex="-1" class="page-wrap">
      <div class="context-bar">
        <nav aria-label="面包屑" class="crumb"><House aria-hidden="true" /><span>{{ admin ? '管理工作区' : '观测工作区' }}</span><template v-if="context"><ArrowRight aria-hidden="true" /><span>{{ context }}</span></template><ArrowRight aria-hidden="true" /><strong aria-current="page">{{ route.meta.title }}</strong></nav>
        <button class="settings-btn" @click="settings = true"><Operation aria-hidden="true" />演示设置</button>
      </div>
      <section v-if="admin && !store.canGovern" class="panel access-wall">
        <Lock aria-hidden="true" /><h1>此账号没有质量治理权限</h1><p>观察账号可访问城市详情和分析工作台。演示设置中可切换具名治理账号。</p>
        <div class="actions"><RouterLink to="/city" class="btn">返回城市详情</RouterLink><button class="btn primary" @click="settings = true">查看演示设置</button></div>
      </section>
      <RouterView v-else />
      <footer class="page-footer"><span>澄空观测<span class="footer-divider">/</span>城市空气质量监测与分析</span><span>F 合成数据 · 本地交互原型</span></footer>
    </main>
    <div class="toast-region" role="status" aria-live="polite" aria-atomic="true"><div v-if="store.toast" class="toast"><Check aria-hidden="true" />{{ store.toast }}<button aria-label="关闭反馈" @click="store.toast = ''"><Close aria-hidden="true" /></button></div></div>
    <ElDialog v-model="settings" title="演示设置" width="min(460px, calc(100% - 32px))" align-center destroy-on-close>
      <p class="dialog-intro">全部样本来自本地 F 合成空间。设置只影响本次预览。</p>
      <fieldset class="form-block"><legend>模拟账号</legend>
        <label class="radio-label"><input type="radio" name="role" :checked="store.role === 'operator'" @change="setRole('operator')" />具名治理账号 <small>可进入管理工作区</small></label>
        <label class="radio-label"><input type="radio" name="role" :checked="store.role === 'reader'" @change="setRole('reader')" />观察账号 <small>城市与分析权限</small></label>
      </fieldset>
      <label v-if="route.name === 'city'" class="field-label" for="city-state">城市样本状态</label>
      <select v-if="route.name === 'city'" id="city-state" :value="store.cityState" @change="selectCityState"><option v-for="(label, key) in cityStates" :key="key" :value="key">{{ label }}</option></select>
      <template #footer><button class="btn primary" @click="settings = false">完成设置</button></template>
    </ElDialog>
    <ElDialog :model-value="Boolean(scopeLabel)" :title="`${scopeLabel} · 原型范围外`" width="min(440px, calc(100% - 32px))" align-center @update:model-value="scopeLabel = ''">
      <p class="dialog-intro">当前原型开放全国总览、城市详情、分析工作台和质量事件详情。</p>
      <div class="scope-links"><RouterLink v-for="item in [{ text:'全国总览', path:'/' },{ text:'城市详情', path:'/city' },{ text:'分析工作台', path:'/analysis' },{ text:'质量事件详情', path:'/quality' }]" :key="item.path" :to="item.path" @click="scopeLabel = ''">{{ item.text }}<ArrowRight aria-hidden="true" /></RouterLink></div>
    </ElDialog>
    <ElDrawer v-model="navigation" title="工作区导航" direction="ltr" size="min(320px, 85vw)">
      <p class="drawer-caption">{{ admin ? '管理工作区' : '观测工作区' }}</p>
      <nav class="drawer-nav" aria-label="窄屏主导航"><template v-for="item in navItems" :key="item.text">
        <RouterLink v-if="item.path" :to="item.path" @click="navigation = false">{{ item.text }}<ArrowRight aria-hidden="true" /></RouterLink>
        <button v-else @click="outOfScope(item.text)">{{ item.text }}<small>范围外</small></button>
      </template>
      <RouterLink v-if="!admin" to="/city" @click="navigation = false">城市详情<ArrowRight aria-hidden="true" /></RouterLink></nav>
      <button class="btn drawer-switch" @click="workspace"><Switch aria-hidden="true" />切换到{{ admin ? '观测' : '管理' }}工作区</button>
      <button class="btn drawer-switch" @click="navigation = false; settings = true"><SetUp aria-hidden="true" />演示设置<ArrowDown aria-hidden="true" /></button>
    </ElDrawer>
  </ElConfigProvider>
</template>
