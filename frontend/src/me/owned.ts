import { onScopeDispose, ref, watch } from 'vue'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'

type Resource = 'favorites' | 'saved-queries' | 'exports'
type OwnedPage<T> = { results: T[]; page: { page_size: number; next_cursor: string | null } }

export function useOwnedPage<T>(resource: Resource, clearLocal?: () => void) {
  const session = useSessionStore()
  const read = usePrivateRead<OwnedPage<T>>()
  const notice = ref('')
  const actionError = ref('')
  const actionBusy = ref(false)
  const cursor = ref<string>()
  const root = `me/${resource}/`
  let active = true

  function list(next?: string) {
    cursor.value = next
    return read.run(signal => api.get<OwnedPage<T>>(root, {
      ...scopeQuery(session.scope), cursor: next, page_size: 25,
    }, signal))
  }

  function item(id: number | string, operation = '') {
    const query = session.scope ? '?scope=synthetic' : ''
    return `${root}${id}/${operation}${query}`
  }

  async function act<TValue>(request: () => Promise<TValue>, accept: (value: TValue) => void, reject?: (error: unknown) => void) {
    if (actionBusy.value) return
    const generation = privateStateGeneration()
    const isCurrent = () => active && generation === privateStateGeneration()
    actionBusy.value = true
    notice.value = ''
    actionError.value = ''
    try {
      const value = await request()
      if (isCurrent()) accept(value)
    } catch (error) {
      if (isCurrent()) {
        actionError.value = error instanceof ApiError ? error.message : '操作未完成，请重试。'
        reject?.(error)
      }
    } finally {
      if (isCurrent()) actionBusy.value = false
    }
  }

  function clearFeedback() {
    actionBusy.value = false
    notice.value = ''
    actionError.value = ''
    cursor.value = undefined
    clearLocal?.()
  }

  window.addEventListener('air-quality:clear-reads', clearFeedback)
  onScopeDispose(() => {
    active = false
    window.removeEventListener('air-quality:clear-reads', clearFeedback)
  })
  watch(() => [session.profile?.username, session.scope] as const, ([username]) => {
    cursor.value = undefined
    if (username) void list()
    else read.clear()
  }, { immediate: true })

  return { ...read, notice, actionError, actionBusy, cursor, list, item, act }
}

export function timeLabel(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return '—'
  return `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}
