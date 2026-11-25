import { onScopeDispose } from 'vue'
import { useRouter } from 'vue-router'
import { type DataScope, useSessionStore } from './session'

type Condition = string | number | boolean | null
export type ResumeDraft = Record<string, Condition | readonly Condition[]>
type DraftKey = { route: symbol; username: string; scope: DataScope }
const routeKeys = new WeakMap<object, symbol>()
const lifetime = 30_000

export function resumeDraftKey(route: object, username: string, scope: DataScope): DraftKey {
  let key = routeKeys.get(route)
  if (!key) {
    key = Symbol()
    routeKeys.set(route, key)
  }
  return { route: key, username, scope }
}

function sameKey(a: DraftKey, b: DraftKey) {
  return a.route === b.route && a.username === b.username && a.scope === b.scope
}

export function createSessionResumeDraft() {
  let provider: { key: DraftKey; capture: () => ResumeDraft | null } | undefined
  let staged: { token: symbol; key: DraftKey; draft: ResumeDraft; expires: number; ready: boolean } | undefined
  let expiry: ReturnType<typeof setTimeout> | undefined

  function discard() {
    staged = undefined
    clearTimeout(expiry)
    expiry = undefined
  }

  function register(key: DraftKey, capture: () => ResumeDraft | null) {
    const entry = { key, capture }
    provider = entry
    return () => { if (provider === entry) provider = undefined }
  }

  function stage(key: DraftKey) {
    if (staged && sameKey(staged.key, key)) {
      if (Date.now() < staged.expires) return staged.token
      discard()
      return
    }
    discard()
    const draft = provider && sameKey(provider.key, key) ? provider.capture() : null
    if (!draft) return
    const token = Symbol()
    const snapshot = Object.fromEntries(Object.entries(draft).map(([field, value]) => [
      field, Array.isArray(value) ? Object.freeze([...value]) : value,
    ])) as ResumeDraft
    staged = { token, key, draft: Object.freeze(snapshot), expires: Date.now() + lifetime, ready: false }
    expiry = setTimeout(discard, lifetime)
    return token
  }

  function complete(token: symbol | undefined, key: DraftKey | undefined) {
    if (!staged || staged.token !== token) return
    if (!key || !sameKey(staged.key, key) || Date.now() >= staged.expires) discard()
    else staged.ready = true
  }

  function take(key: DraftKey) {
    if (!staged?.ready || !sameKey(staged.key, key)) return null
    const draft = Date.now() < staged.expires ? staged.draft : null
    discard()
    return draft
  }

  return { register, stage, complete, take, discard }
}

export const sessionResumeDraft = createSessionResumeDraft()

export function useSessionResumeDraft<T extends ResumeDraft>(capture: () => T | null): Readonly<T> | null {
  const router = useRouter()
  const session = useSessionStore()
  const username = session.profile?.username
  if (!username || !session.verified) return null
  const key = resumeDraftKey(router.currentRoute.value, username, session.scope)
  const restored = sessionResumeDraft.take(key) as Readonly<T> | null
  // RouterView may reuse this instance on a query-only navigation. When staging,
  // bind its then-current conditions to the exact then-current route object.
  let unregister = sessionResumeDraft.register(key, capture)
  const unwatch = router.afterEach((route, _from, failure) => {
    if (failure || route.name !== routeName || session.profile?.username !== username || session.scope !== key.scope) return
    unregister()
    unregister = sessionResumeDraft.register(resumeDraftKey(route, username, session.scope), capture)
  })
  const routeName = router.currentRoute.value.name
  onScopeDispose(() => { unregister(); unwatch() })
  return restored
}
