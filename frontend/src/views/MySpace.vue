<script setup lang="ts">
import { onScopeDispose, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, api, privateStateGeneration } from '../api/client'
import ExportTicketsPanel from '../me/ExportTicketsPanel.vue'
import FavoritesPanel from '../me/FavoritesPanel.vue'
import SavedQueriesPanel from '../me/SavedQueriesPanel.vue'
import { type Profile, useSessionStore } from '../stores/session'

const session = useSessionStore()
const router = useRouter()
const profile = reactive({ first_name: session.profile?.first_name ?? '', last_name: session.profile?.last_name ?? '' })
const password = reactive({ old_password: '', new_password: '' })
const message = ref('')
const error = ref('')
const busy = ref(false)

let active = true

function clearForm() {
  password.old_password = ''
  password.new_password = ''
  message.value = ''
  error.value = ''
  busy.value = false
}

window.addEventListener('air-quality:clear-reads', clearForm)
onScopeDispose(() => {
  active = false
  clearForm()
  window.removeEventListener('air-quality:clear-reads', clearForm)
})
watch(() => session.profile, person => {
  profile.first_name = person?.first_name ?? ''
  profile.last_name = person?.last_name ?? ''
})

async function writeAccount(change: () => Promise<void>, fallback: string) {
  if (busy.value) return
  const generation = privateStateGeneration()
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await change()
  } catch (cause) {
    if (active && generation === privateStateGeneration()) {
      error.value = cause instanceof ApiError ? cause.message : fallback
    }
  } finally {
    if (active && generation === privateStateGeneration()) busy.value = false
  }
}

function saveProfile() {
  void writeAccount(async () => {
    const result = await api.patch<Profile>('me/', { ...profile })
    if (!active) return
    session.profile = result
    message.value = '个人资料已更新。'
  }, '资料未能更新。')
}

function changePassword() {
  void writeAccount(async () => {
    await api.post('auth/password/', { ...password })
    session.clearPrivate()
    await router.replace({ name: 'login' })
  }, '密码未能更新。')
}
</script>

<template>
  <div class="page-content my-space">
    <header class="section-head">
      <div><h1>我的空间</h1><p>当前账号：{{ session.profile?.username }}</p></div>
    </header>
    <nav class="me-actions me-nav" aria-label="我的空间页内导航">
      <a class="btn" href="#my-favorites-title">我的关注</a>
      <a class="btn" href="#my-saved-title">分析方案</a>
      <a class="btn" href="#my-exports-title">近期导出</a>
      <a class="btn" href="#my-account-title">账号设置</a>
    </nav>
    <FavoritesPanel />
    <SavedQueriesPanel />
    <ExportTicketsPanel />
    <section class="me-panel" aria-labelledby="my-account-title">
      <header class="me-panel-head"><h2 id="my-account-title">账号设置</h2></header>
      <p v-if="message" class="status status-success" role="status">{{ message }}</p>
      <p v-if="error" class="status status-error" role="alert">{{ error }}</p>
      <div class="form-section">
        <h3>个人资料</h3>
        <form class="settings-form" @submit.prevent="saveProfile">
          <label class="field">姓<input v-model="profile.last_name" maxlength="150" autocomplete="family-name" /></label>
          <label class="field">名<input v-model="profile.first_name" maxlength="150" autocomplete="given-name" /></label>
          <button class="btn primary" type="submit" :disabled="busy">保存资料</button>
        </form>
      </div>
      <div class="form-section me-last-form">
        <h3>修改密码</h3>
        <form class="settings-form" @submit.prevent="changePassword">
          <label class="field">当前密码<input v-model="password.old_password" type="password" autocomplete="current-password" maxlength="1024" required /></label>
          <label class="field">新密码<input v-model="password.new_password" type="password" autocomplete="new-password" maxlength="1024" required /></label>
          <p class="form-hint">修改密码后，所有已登录会话都会失效。</p>
          <button class="btn primary" type="submit" :disabled="busy">更新密码并重新登录</button>
        </form>
      </div>
    </section>
  </div>
</template>

<style>
.my-space { min-width: 0; }
.my-space .section-head > div { min-width: 0; overflow-wrap: anywhere; }
.my-space .me-nav { margin: -5px 0 0; }
.my-space .me-panel { display: grid; gap: 15px; min-width: 0; padding: 18px 0 8px; border-top: 1px solid var(--aq-border); }
.my-space .me-panel h2 { scroll-margin-top: 125px; }
.my-space .me-panel :is(h2, h3, h4, p) { margin: 0; overflow-wrap: anywhere; }
.my-space .me-panel h3 { margin-bottom: 4px; }
.my-space .me-panel-head, .my-space .me-entry-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px 16px; }
.my-space .me-panel-head > div, .my-space .me-entry-head > div { min-width: 0; }
.my-space .me-panel-head p, .my-space .me-entry-head p, .my-space .me-muted, .my-space .me-empty { color: var(--aq-secondary); }
.my-space .me-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.my-space .me-actions .btn, .my-space .me-panel-head > .btn { min-height: 44px; }
.my-space .me-fit { justify-self: start; }
.my-space .me-list, .my-space .me-epochs { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.my-space .me-row { min-width: 0; padding: 15px 0; border-top: 1px solid var(--aq-border); }
.my-space .me-row:last-child { padding-bottom: 0; }
.my-space .me-entry { display: grid; gap: 12px; min-width: 0; }
.my-space .me-readings { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 190px), 1fr)); gap: 8px; }
.my-space .me-reading { display: grid; align-content: start; gap: 2px; min-width: 0; padding: 9px 10px; border: 1px solid var(--aq-border); border-radius: 8px; overflow-wrap: anywhere; }
.my-space .me-reading span { color: var(--aq-secondary); font-size: 13px; }
.my-space .me-details { padding: 12px; border: 1px solid var(--aq-border); border-radius: 8px; }
.my-space .me-details summary { min-height: 32px; color: var(--aq-link); cursor: pointer; }
.my-space .me-details > p + .me-actions { margin-top: 8px; }
.my-space .me-epochs li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; padding: 8px 0; border-top: 1px solid var(--aq-border); overflow-wrap: anywhere; }
.my-space .me-edit { display: grid; gap: 12px; max-width: 40rem; }
.my-space .me-pagination { margin-top: 2px; }
.my-space .me-last-form { border: 0; padding-bottom: 0; }
@media (max-width: 640px) {
  .my-space .me-panel-head, .my-space .me-entry-head { flex-direction: column; }
}
</style>
