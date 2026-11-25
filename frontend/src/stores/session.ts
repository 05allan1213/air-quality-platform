import { defineStore } from 'pinia'
import { computed, nextTick, onScopeDispose, ref } from 'vue'
import { ApiError, api } from '../api/client'

export interface Profile {
  username: string
  first_name: string
  last_name: string
}

export interface Capabilities {
  workspaces: ('user' | 'manage')[]
  actions: string[]
}

export type DataScope = '' | 'synthetic'

export function scopeQuery(scope: DataScope) {
  return scope ? { scope } : {}
}

const logoutKey = 'aq:logout-pending'
const logoutCookie = 'aq_logout_pending'
const logoutCookieLifetime = 8 * 60 * 60 // Match Django's SESSION_COOKIE_AGE.

function writeLogoutCookie(pending: boolean) {
  const secure = location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${logoutCookie}=1; Path=/app/; SameSite=Strict; Max-Age=${pending ? logoutCookieLifetime : 0}${secure}`
}

function storedLogoutPending() {
  try {
    if (localStorage.getItem(logoutKey) === '1') return true
  } catch { /* The cookie may still be available. */ }
  try {
    return document.cookie.split(';').some(cookie => cookie.trim() === `${logoutCookie}=1`)
  } catch {
    return false
  }
}

function restoreLogoutPending() {
  const pending = storedLogoutPending()
  try {
    if (sessionStorage.getItem(logoutKey) !== '1') return pending
  } catch {
    return pending
  }
  // Promote the former tab-only fallback to the shared marker on upgrade.
  if (!pending) {
    try { writeLogoutCookie(true) } catch { /* Keep the existing tab lock. */ }
  }
  if (storedLogoutPending()) {
    try { sessionStorage.removeItem(logoutKey) } catch { /* Confirmed logout will retry cleanup. */ }
  }
  return true
}

export const useSessionStore = defineStore('session', () => {
  const profile = ref<Profile | null>(null)
  const capabilities = ref<Capabilities>({ workspaces: ['user'], actions: [] })
  const initialized = ref(false)
  const loading = ref(false)
  const verificationFailed = ref(false)
  const scope = ref<DataScope>('')
  const scopeInitialized = ref(false)
  const logoutPending = ref(restoreLogoutPending())
  const authenticated = computed(() => profile.value !== null)
  const verified = computed(() => authenticated.value && !loading.value && !verificationFailed.value && !logoutPending.value)
  let inFlight: { controller: AbortController; promise: Promise<void> } | undefined

  function enterScope(next: DataScope) {
    if (next !== scope.value) {
      // Discard late reads in this tab; another tab may still be using the other domain.
      api.clearPrivateState(false)
      scope.value = next
    }
    scopeInitialized.value = true
  }

  function cancelVerification() {
    inFlight?.controller.abort()
    inFlight = undefined
    loading.value = false
  }

  function clearPrivate(broadcast = true) {
    cancelVerification()
    profile.value = null
    capabilities.value = { workspaces: ['user'], actions: [] }
    verificationFailed.value = false
    scope.value = ''
    scopeInitialized.value = false
    api.clearPrivateState(broadcast)
  }

  function refresh(): Promise<void> {
    if (logoutPending.value) return Promise.resolve()
    if (inFlight) return inFlight.promise
    const controller = new AbortController()
    const previousUsername = profile.value?.username
    loading.value = true

    async function verify() {
      // Let the shell remove private components and cancel their reads before requesting a fresh projection.
      await nextTick()
      if (controller.signal.aborted) return
      try {
        const [person, access] = await Promise.all([
          api.get<Profile>('me/', undefined, controller.signal),
          api.get<Capabilities>('me/capabilities/', undefined, controller.signal),
        ])
        if (controller.signal.aborted) return
        if (!person?.username || !Array.isArray(access?.actions)) {
          throw new ApiError('无法核验会话响应。', 502, 'invalid_response')
        }
        if (previousUsername && previousUsername !== person.username) {
          // A different browser session cannot adopt this tab's routes or in-flight commands.
          clearPrivate(false)
          initialized.value = true
          return
        }
        profile.value = person
        capabilities.value = access
        verificationFailed.value = false
      } catch (error) {
        if (controller.signal.aborted) return
        if (error instanceof ApiError && error.status === 403 && error.code === 'not_authenticated') {
          clearPrivate(false)
          initialized.value = true
          return
        }
        controller.abort()
        verificationFailed.value = true
        throw error
      } finally {
        if (inFlight?.controller === controller) {
          inFlight = undefined
          initialized.value = true
          loading.value = false
        }
      }
    }

    const promise = verify()
    inFlight = { controller, promise }
    return promise
  }

  async function ensureLoaded(recheck = false) {
    // A suspended tab may not have processed its logout notification yet.
    if (!logoutPending.value && storedLogoutPending()) synchronizeLogout()
    if (logoutPending.value) return
    if (inFlight) await inFlight.promise
    else if (!initialized.value || verificationFailed.value || recheck) await refresh()
  }

  async function signIn(username: string, password: string) {
    clearPrivate()
    initialized.value = true
    await api.refreshCsrf()
    await api.post('auth/login/', { username, password })
    await api.refreshCsrf()
    initialized.value = false
    await refresh()
  }

  async function register(username: string, password: string) {
    await api.refreshCsrf()
    await api.post('auth/register/', { username, password })
  }

  function markLogout(pending: boolean) {
    logoutPending.value = pending
    if (pending) {
      try { localStorage.setItem(logoutKey, '1') } catch {
        try { writeLogoutCookie(true) } catch { /* The in-memory lock remains active for this tab. */ }
        if (!storedLogoutPending()) {
          try { sessionStorage.setItem(logoutKey, '1') } catch { /* No persistent storage is available. */ }
        }
      }
    } else {
      try { localStorage.removeItem(logoutKey) } catch { /* Still clear the cookie fallback. */ }
      try { writeLogoutCookie(false) } catch { /* Browser cookie writes are unavailable. */ }
      try { sessionStorage.removeItem(logoutKey) } catch { /* Browser tab storage is unavailable. */ }
    }
    api.notifyLogoutState()
  }

  async function signOut() {
    markLogout(true)
    clearPrivate()
    initialized.value = true
    try {
      await api.post('auth/logout/')
    } catch {
      // A lost response can follow a committed logout. Only a current server denial settles it.
      try {
        await api.get<Profile>('me/')
        return false
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 403 && error.code === 'not_authenticated')) return false
      }
    }
    markLogout(false)
    return true
  }

  function synchronizeLogout() {
    const pending = storedLogoutPending()
    if (pending === logoutPending.value) return
    logoutPending.value = pending
    window.dispatchEvent(new Event('air-quality:session-expired'))
  }

  function handleStorage(event: StorageEvent) {
    if (event.key === logoutKey) synchronizeLogout()
  }

  function handleSessionExpired() {
    clearPrivate(false)
    initialized.value = true
  }

  window.addEventListener('storage', handleStorage)
  window.addEventListener('air-quality:logout-state', synchronizeLogout)
  window.addEventListener('air-quality:session-expired', handleSessionExpired)
  onScopeDispose(() => {
    cancelVerification()
    window.removeEventListener('storage', handleStorage)
    window.removeEventListener('air-quality:logout-state', synchronizeLogout)
    window.removeEventListener('air-quality:session-expired', handleSessionExpired)
  })

  return { profile, capabilities, initialized, loading, authenticated, verificationFailed, verified, logoutPending, scope, scopeInitialized, enterScope, refresh, ensureLoaded, signIn, register, signOut, clearPrivate }
})
