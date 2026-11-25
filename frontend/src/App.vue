<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CircleCheck, Close, Collection, DataAnalysis, Document, Expand, Fold, InfoFilled, Menu, Odometer, Search, Setting, Sunny, User } from '@element-plus/icons-vue'
import { canEnter } from './router'
import { scopeQuery, useSessionStore } from './stores/session'
import { resumeDraftKey, sessionResumeDraft } from './stores/sessionResumeDraft'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const narrowViewport = window.matchMedia('(max-width: 900px)')
const solidMaterialMedia = window.matchMedia('(prefers-reduced-transparency: reduce), (prefers-reduced-motion: reduce), (forced-colors: active)')
const narrow = ref(narrowViewport.matches)
const drawerOpen = ref(false)
const desktopCollapsed = ref(false)
const solidMaterial = ref(false)
const systemSolidMaterial = ref(solidMaterialMedia.matches)
const useSolidMaterial = computed(() => solidMaterial.value || systemSolidMaterial.value)
const navToggle = ref<HTMLButtonElement>()
const drawerClose = ref<HTMLButtonElement>()
const sidebar = ref<HTMLElement>()
const main = ref<HTMLElement>()
const materialPreferenceKey = 'aq:solid-material'
const navExpanded = computed(() => narrow.value ? drawerOpen.value : !desktopCollapsed.value)
const navToggleLabel = computed(() => narrow.value ? '打开导航' : desktopCollapsed.value ? '展开侧栏' : '收起侧栏')
const signingOut = ref(false)
const isPublic = computed(() => route.meta.public === true)
const restoringPage = ref(false)
const showPrivate = computed(() => session.verified && !restoringPage.value)
let wasAway = document.visibilityState === 'hidden' || !document.hasFocus()
let resumeCheckRunning = false
let resumeInterrupted = false
let resumeToken: symbol | undefined

const workspaceQuery = computed(() => scopeQuery(session.scope))

function changeScope(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value !== '' && value !== 'synthetic') return
  if (value !== session.scope) void router.replace({ name: 'overview', query: scopeQuery(value) })
}

const nav = computed(() => {
  const items = [
    { name: 'overview', label: '监测总览', icon: Odometer },
    { name: 'analysis', label: '分析工作台', icon: DataAnalysis },
    { name: 'query', label: '数据查询', icon: Search },
    { name: 'me', label: '我的空间', icon: User },
    { name: 'manage-collection', label: '采集任务', icon: Collection },
    { name: 'manage-quality', label: '数据质量', icon: CircleCheck },
    { name: 'manage-operations', label: '系统运行', icon: Setting },
  ]
  return items.filter(item => canEnter(item.name, showPrivate.value ? session.capabilities.actions : []))
})

async function toggleNavigation() {
  if (!narrow.value) {
    desktopCollapsed.value = !desktopCollapsed.value
    return
  }
  drawerOpen.value = true
  await nextTick()
  drawerClose.value?.focus()
}

async function closeNavigation() {
  drawerOpen.value = false
  await nextTick()
  navToggle.value?.focus()
}

function handleDrawerKeys(event: KeyboardEvent) {
  if (!drawerOpen.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    void closeNavigation()
    return
  }
  if (event.key !== 'Tab') return
  const controls = sidebar.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]')
  if (!controls?.length) return
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

async function resizeNavigation(event: MediaQueryListEvent) {
  const active = document.activeElement
  const focusNeedsMove = Boolean(event.matches ? sidebar.value?.contains(active) : active === drawerClose.value)
  narrow.value = event.matches
  drawerOpen.value = false
  if (focusNeedsMove) {
    await nextTick()
    navToggle.value?.focus()
  }
}

function toggleMaterial() {
  solidMaterial.value = !solidMaterial.value
  try { window.localStorage.setItem(materialPreferenceKey, solidMaterial.value ? '1' : '0') } catch { /* Keep the in-memory preference. */ }
}

function setSystemMaterial(event: MediaQueryListEvent) {
  systemSolidMaterial.value = event.matches
}

watch(() => route.fullPath, async () => {
  if (!drawerOpen.value) return
  drawerOpen.value = false
  await nextTick()
  main.value?.focus({ preventScroll: true })
})

async function signOut() {
  signingOut.value = true
  try {
    if (await session.signOut()) await router.replace({ name: 'login' })
  } finally {
    signingOut.value = false
  }
}

function handleSessionExpired() {
  if (route.matched.length && !isPublic.value) void router.replace({ name: 'login' })
}

// Compare route identity and account values. A refreshed profile can represent the same user.
watch([() => router.currentRoute.value, () => session.profile?.username, () => session.scope, () => session.logoutPending],
  sessionResumeDraft.discard, { flush: 'sync' })

function currentDraftKey() {
  const username = session.profile?.username
  if (session.verified && username && canEnter(route.name, session.capabilities.actions)) {
    return resumeDraftKey(router.currentRoute.value, username, session.scope)
  }
}

function stageDraft() {
  const key = currentDraftKey()
  if (key) return sessionResumeDraft.stage(key)
}

function canRevalidatePage() {
  return !isPublic.value && route.matched.length > 0 && !session.logoutPending
}

function revalidatePage(preserveDraft = false) {
  if (resumeCheckRunning) return
  if (!canRevalidatePage()) {
    sessionResumeDraft.discard()
    resumeToken = undefined
    restoringPage.value = false
    return
  }
  if (preserveDraft) resumeToken ??= stageDraft()
  restoringPage.value = true
  void verifyPage()
}

async function verifyPage() {
  resumeCheckRunning = true
  let waitingForReturn = false
  try {
    while (canRevalidatePage()) {
      resumeInterrupted = false
      const checked = await session.ensureLoaded(true).then(() => true, () => false)
      if (!canRevalidatePage()) break
      // A check started before the latest departure cannot reopen the workspace.
      if (resumeInterrupted) {
        if (!wasAway) continue
        waitingForReturn = true
        return
      }
      if (checked) {
        sessionResumeDraft.complete(resumeToken, currentDraftKey())
        if (!session.verified) void router.replace({ name: 'login' })
      } else {
        sessionResumeDraft.discard()
      }
      return
    }
    sessionResumeDraft.discard()
  } finally {
    resumeCheckRunning = false
    if (!waitingForReturn) {
      resumeToken = undefined
      restoringPage.value = false
    }
  }
}

function markAway() {
  wasAway = true
  if (resumeCheckRunning) resumeInterrupted = true
}

function handleResume() {
  if (!wasAway || document.visibilityState !== 'visible') return
  wasAway = false
  void revalidatePage(true)
}

function handleVisibility() {
  if (document.visibilityState === 'hidden') markAway()
  else handleResume()
}

function handlePageHide(event: PageTransitionEvent) {
  if (event.persisted && !isPublic.value) {
    stageDraft()
    restoringPage.value = true
    markAway()
  }
}

function handlePageRestore(event: PageTransitionEvent) {
  if (!event.persisted) return
  markAway()
  handleResume()
}

onMounted(() => {
  narrowViewport.addEventListener('change', resizeNavigation)
  solidMaterialMedia.addEventListener('change', setSystemMaterial)
  systemSolidMaterial.value = solidMaterialMedia.matches
  try { solidMaterial.value = window.localStorage.getItem(materialPreferenceKey) === '1' } catch { /* Default to system preferences. */ }
  window.addEventListener('air-quality:session-expired', handleSessionExpired)
  window.addEventListener('blur', markAway)
  window.addEventListener('focus', handleResume)
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('pagehide', handlePageHide)
  window.addEventListener('pageshow', handlePageRestore)
})
onUnmounted(() => {
  sessionResumeDraft.discard()
  narrowViewport.removeEventListener('change', resizeNavigation)
  solidMaterialMedia.removeEventListener('change', setSystemMaterial)
  window.removeEventListener('air-quality:session-expired', handleSessionExpired)
  window.removeEventListener('blur', markAway)
  window.removeEventListener('focus', handleResume)
  document.removeEventListener('visibilitychange', handleVisibility)
  window.removeEventListener('pagehide', handlePageHide)
  window.removeEventListener('pageshow', handlePageRestore)
})
</script>

<template>
  <main v-if="session.logoutPending" class="auth-page">
    <section class="auth-panel panel" aria-labelledby="logout-title">
      <h1 id="logout-title">{{ signingOut ? '正在退出' : '退出尚未确认' }}</h1>
      <p class="auth-intro" role="status">{{ signingOut ? '正在核验服务端会话。' : '此设备已隐藏私有内容。请重试，确认服务端会话结束后再离开。' }}</p>
      <button class="btn primary" type="button" :disabled="signingOut" @click="signOut">重新退出</button>
    </section>
  </main>
  <RouterView v-else-if="isPublic" />
  <div v-else class="application" :class="{ 'drawer-active': drawerOpen }" :data-material="useSolidMaterial ? 'solid' : undefined">
    <a class="skip-link" href="#main-content" :inert="drawerOpen">跳到主要内容</a>
    <header class="topbar" :inert="drawerOpen">
      <div class="topbar-inner">
        <button ref="navToggle" class="icon-button menu-toggle" type="button" :aria-expanded="navExpanded"
          :aria-label="navToggleLabel" :title="navToggleLabel" aria-controls="workspace-sidebar" @click="toggleNavigation">
          <el-icon aria-hidden="true"><component :is="narrow ? Menu : desktopCollapsed ? Expand : Fold" /></el-icon>
        </button>
        <RouterLink class="brand" :to="{ name: 'overview', query: workspaceQuery }">
          <span class="brand-mark" aria-hidden="true"><el-icon><Odometer /></el-icon></span>
          <span class="brand-name">城市空气质量平台</span>
        </RouterLink>
        <div class="topbar-tools">
          <label class="scope-control" for="data-scope">数据域</label>
          <select id="data-scope" :value="session.scope" aria-label="选择数据域" @change="changeScope">
            <option value="">正式</option>
            <option value="synthetic">F 合成</option>
          </select>
          <RouterLink v-if="showPrivate" class="account-link" :to="{ name: 'me', query: workspaceQuery }">{{ session.profile?.username }}</RouterLink>
          <button class="icon-button material-toggle" type="button" :aria-pressed="solidMaterial" aria-label="实体主题" title="实体主题" @click="toggleMaterial">
            <el-icon aria-hidden="true"><Sunny /></el-icon>
          </button>
          <button class="text-button" type="button" :disabled="signingOut" @click="signOut">退出</button>
        </div>
      </div>
    </header>
    <div class="workspace" :class="{ 'workspace-collapsed': !narrow && desktopCollapsed }">
      <div v-if="drawerOpen" class="nav-scrim" aria-hidden="true" @click="closeNavigation" />
      <aside id="workspace-sidebar" ref="sidebar" class="sidebar" :class="{ 'sidebar-open': drawerOpen }"
        :inert="narrow && !drawerOpen" :role="drawerOpen ? 'dialog' : undefined" :aria-modal="drawerOpen || undefined"
        :aria-labelledby="drawerOpen ? 'workspace-heading' : undefined" @keydown="handleDrawerKeys">
        <div class="sidebar-head">
          <div id="workspace-heading" class="workspace-heading">{{ route.meta.manage ? '管理工作区' : '观测工作区' }}</div>
          <button v-if="drawerOpen" ref="drawerClose" class="icon-button drawer-close" type="button" aria-label="关闭导航" @click="closeNavigation">
            <el-icon aria-hidden="true"><Close /></el-icon>
          </button>
        </div>
        <nav aria-label="工作区导航">
          <RouterLink v-for="item in nav" :key="item.name" :to="{ name: item.name, query: workspaceQuery }"
            :aria-label="item.label" :title="item.label" class="nav-link" active-class="nav-active"
            @click="drawerOpen && route.name === item.name && closeNavigation()">
            <el-icon aria-hidden="true"><component :is="item.icon" /></el-icon><span class="nav-text">{{ item.label }}</span>
          </RouterLink>
        </nav>
        <div class="sidebar-foot">
          <RouterLink :to="{ name: 'about' }" class="nav-link" aria-label="平台说明" title="平台说明"><el-icon aria-hidden="true"><InfoFilled /></el-icon><span class="nav-text">平台说明</span></RouterLink>
          <RouterLink :to="{ name: 'sources' }" class="nav-link" aria-label="数据与来源" title="数据与来源"><el-icon aria-hidden="true"><Document /></el-icon><span class="nav-text">数据与来源</span></RouterLink>
        </div>
      </aside>
      <main id="main-content" ref="main" class="page-wrap" tabindex="-1" :inert="drawerOpen">
        <div v-if="!session.initialized || session.loading || restoringPage" class="session-state" role="status">正在核验当前会话…</div>
        <div v-else-if="session.verificationFailed" class="session-state">
          <p role="alert">暂时无法核验当前会话。私有内容已隐藏。</p>
          <button class="btn" type="button" @click="revalidatePage()">重新核验</button>
        </div>
        <RouterView v-else-if="showPrivate && canEnter(route.name, session.capabilities.actions)" :key="session.scope" />
        <section v-else-if="showPrivate" class="session-state" aria-labelledby="workspace-access-title">
          <h1 id="workspace-access-title">当前账号无权访问此工作区</h1>
          <RouterLink class="btn" :to="{ name: 'overview', query: workspaceQuery }">返回监测总览</RouterLink>
        </section>
      </main>
    </div>
  </div>
</template>
