import { ref } from 'vue'
import { api, usePrivateRead } from '../api/client'

export type ApiTarget = { path: string; params: Record<string, string> }
export type PageInfo = { page_size: number; next_cursor: string | null }

export function localApiHref(href: string): ApiTarget | null {
  if (!href.startsWith('/api/v1/')) return null
  const url = new URL(href, window.location.origin)
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/')) return null
  return { path: url.pathname.slice('/api/v1/'.length), params: Object.fromEntries(url.searchParams) }
}

export function useMonitorPages<T extends { page: PageInfo }>() {
  const read = usePrivateRead<T>()
  const hasCursor = ref(false)
  let target: ApiTarget | null = null

  function load(cursor?: string) {
    if (!target) return
    const { path, params } = target
    hasCursor.value = Boolean(cursor)
    void read.run(signal => api.get<T>(path, { ...params, cursor }, signal))
  }

  function open(selection: ApiTarget) {
    target = selection
    load()
  }

  function clear() {
    target = null
    hasCursor.value = false
    read.clear()
  }

  function next() {
    const cursor = read.data.value?.page.next_cursor
    if (cursor) load(cursor)
  }

  return { ...read, hasCursor, open, clear, first: () => load(), next }
}

export type Favorite = { id: number; kind: string; target_id?: number; product?: number; state: string }

export async function readFavorite(kind: string, target: string, product: string, scope: string | undefined, signal: AbortSignal) {
  let cursor: string | null = null
  do {
    const page: { results: Favorite[]; page: PageInfo } = await api.get('me/favorites/', { scope, cursor, page_size: 100 }, signal)
    const item = page.results.find(item => item.kind === kind && String(item.target_id) === target && String(item.product) === product)
    if (item) return item
    cursor = page.page.next_cursor
  } while (cursor)
  return null
}
