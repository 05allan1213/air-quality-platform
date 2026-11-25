import axios, { type AxiosRequestConfig } from 'axios'
import { onScopeDispose, ref, type Ref } from 'vue'

const http = axios.create({
  baseURL: '/api/v1/',
  withCredentials: true,
  timeout: 15_000,
  responseType: 'text',
  transformResponse: [(body: unknown) => body],
  headers: { Accept: 'application/json' },
})

let csrfToken = ''
let csrfRequest: Promise<string> | undefined
let privateGeneration = 0
const privateStateChannel = typeof BroadcastChannel === 'undefined'
  ? undefined
  : new BroadcastChannel('aq-private-state')

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly runId?: string,
    readonly attemptId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const messages: Record<string, string> = {
  not_authenticated: '登录状态已失效，请重新登录。',
  permission_denied: '当前账号无权执行此操作。',
  use_denied: '当前数据用途不在授权范围内。',
  not_found: '无法访问此内容，或内容不存在。',
  invalid_request: '请求条件无效，请检查输入后重试。',
  invalid_credentials: '用户名或密码不正确。',
  rate_limited: '请求过于频繁，请稍后再试。',
  query_too_large: '查询范围过大，请缩短时段或减少所选对象。',
  input_changed: '输入版本已变化，请重新应用条件。',
  expired: '这份下载票据已过期。',
  unsafe_integer: '服务返回了当前浏览器不能精确表示的整数，已停止本次操作。',
  invalid_response: '服务未返回预期的数据格式。',
  csrf_failed: '安全校验已更新，请重新提交操作。',
  page_changed: '结果或权限已变化，请从第一页重新查询。',
  incompatible_series: '所选数据系列不兼容，请调整条件。',
  database_unavailable: '数据服务暂不可用，请稍后重试。',
}

function failure(status: number, code: string, runId?: string, attemptId?: string) {
  const message = messages[code] ?? (status ? '请求未完成，请检查条件或稍后重试。' : '无法连接服务，请确认本地服务状态。')
  return new ApiError(message, status, code, runId, attemptId)
}

export function decodeJson(text: unknown): unknown {
  if (typeof text !== 'string') throw failure(502, 'invalid_response')
  try {
    return JSON.parse(text, (_key: string, value: unknown) => {
      if (typeof value === 'number' && (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value)))) {
        throw failure(422, 'unsafe_integer')
      }
      return value
    })
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw failure(502, 'invalid_response')
  }
}

export function apiPath(path: string) {
  if (!/^[a-z][\w/-]*\/(?:\?[^#\\]*)?$/.test(path) || path.includes('//')) throw failure(400, 'invalid_request')
  return path
}

async function response(config: AxiosRequestConfig, generation: number) {
  const ensureCurrent = () => {
    if (generation !== privateGeneration || config.signal?.aborted) throw failure(0, 'superseded')
  }
  ensureCurrent()
  try {
    const result = await http.request<string>({ ...config, url: apiPath(config.url ?? '') })
    ensureCurrent()
    return result
  } catch (error) {
    ensureCurrent()
    if (!axios.isAxiosError(error)) throw error
    const status = error.response?.status ?? 0
    let parsed = failure(status, status ? 'request_failed' : 'network_error')
    if (String(error.response?.headers['content-type']).startsWith('application/json')) {
      const body = decodeJson(error.response?.data) as { code?: unknown; run_id?: unknown; attempt_id?: unknown } | null
      if (typeof body?.code === 'string') {
        const attemptId = config.method === 'post' && config.url === 'admin/history-imports/'
          && status === 503 && body.code === 'dispatch_unconfirmed' && typeof body.attempt_id === 'string'
          && /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/.test(body.attempt_id) ? body.attempt_id : undefined
        parsed = failure(status, body.code, typeof body.run_id === 'string' ? body.run_id : undefined, attemptId)
      }
    }
    if (status === 403 && parsed.code === 'not_authenticated') {
      privateStateChannel?.postMessage({ kind: 'clear' })
      window.dispatchEvent(new Event('air-quality:session-expired'))
    }
    if (status === 403 && parsed.code === 'csrf_failed') csrfToken = '' // Refresh before an explicit resubmission; never replay writes.
    throw parsed
  }
}

async function json<T>(config: AxiosRequestConfig, generation = privateGeneration): Promise<T> {
  const result = await response(config, generation)
  if (result.status === 204) return undefined as T
  if (!String(result.headers['content-type']).startsWith('application/json')) throw failure(502, 'invalid_response')
  return decodeJson(result.data) as T
}

function refreshCsrf(): Promise<string> {
  if (csrfRequest) return csrfRequest
  const generation = privateGeneration
  const pending = json<{ csrf_token: string }>({ url: 'auth/csrf/' }, generation).then(body => {
    if (generation !== privateGeneration) throw failure(0, 'superseded')
    if (!body || typeof body.csrf_token !== 'string' || !body.csrf_token) throw failure(502, 'invalid_response')
    csrfToken = body.csrf_token
    return csrfToken
  }).finally(() => { if (csrfRequest === pending) csrfRequest = undefined })
  return (csrfRequest = pending)
}

async function write<T>(url: string, data?: unknown, signal?: AbortSignal, method = 'post') {
  const generation = privateGeneration
  const token = csrfToken || await refreshCsrf()
  return json<T>({ url, method, data, signal, headers: { 'X-CSRFToken': token } }, generation)
}

export const api = {
  async get<T>(path: string, params?: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
    return json<T>({ url: path, params, signal, headers: { 'Cache-Control': 'no-store' } })
  },
  post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return write<T>(path, body, signal)
  },
  patch<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return write<T>(path, body, signal, 'patch')
  },
  delete(path: string): Promise<void> {
    return write<void>(path, undefined, undefined, 'delete')
  },
  async downloadCsv(id: string, scope: string, signal?: AbortSignal) {
    if (!/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(id)) throw failure(400, 'invalid_request')
    const generation = privateGeneration
    const result = await response({ url: `me/exports/${id}/download/`, params: scope ? { scope } : {}, signal, headers: { Accept: 'text/csv, application/json' } }, generation)
    if (generation !== privateGeneration || signal?.aborted) throw failure(0, 'superseded')
    if (!/^text\/csv(?:;|$)/i.test(String(result.headers['content-type'])) || !/^attachment\b/i.test(String(result.headers['content-disposition']))) throw failure(502, 'invalid_response')
    const url = URL.createObjectURL(new Blob([result.data], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'air-quality.csv'
    try { link.click() } finally { setTimeout(() => URL.revokeObjectURL(url), 0) }
  },
  refreshCsrf,
  notifyLogoutState() {
    privateStateChannel?.postMessage({ kind: 'logout-state' })
  },
  clearPrivateState(broadcast = true) {
    privateGeneration += 1
    csrfToken = ''
    csrfRequest = undefined
    window.dispatchEvent(new Event('air-quality:clear-reads'))
    if (broadcast) privateStateChannel?.postMessage({ kind: 'clear' })
  },
}

privateStateChannel?.addEventListener('message', (event: MessageEvent<unknown>) => {
  if (!event.data || typeof event.data !== 'object') return
  const { kind } = event.data as { kind?: unknown }
  if (kind === 'logout-state') {
    window.dispatchEvent(new Event('air-quality:logout-state'))
    return
  }
  if (kind !== 'clear') return
  privateGeneration += 1
  csrfToken = ''
  csrfRequest = undefined
  window.dispatchEvent(new Event('air-quality:clear-reads'))
  window.dispatchEvent(new Event('air-quality:session-expired'))
})

export function privateStateGeneration(): number {
  return privateGeneration
}

export function usePrivateRead<T>() {
  const data: Ref<T | null> = ref(null) as Ref<T | null>
  const error: Ref<ApiError | null> = ref(null)
  const busy = ref(false)
  let controller: AbortController | null = null
  let localGeneration = 0

  function clear() {
    localGeneration += 1
    controller?.abort()
    controller = null
    data.value = null
    error.value = null
    busy.value = false
  }

  async function run(load: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> {
    controller?.abort()
    controller = new AbortController()
    const active = controller
    const generation = ++localGeneration
    const session = privateStateGeneration()
    data.value = null
    error.value = null
    busy.value = true
    try {
      const result = await load(active.signal)
      if (generation === localGeneration && session === privateStateGeneration() && !active.signal.aborted) {
        data.value = result
        return result
      }
      return undefined
    } catch (cause) {
      if (generation === localGeneration && session === privateStateGeneration() && !active.signal.aborted) {
        error.value = cause instanceof ApiError ? cause : failure(0, 'network_error')
      }
      return undefined
    } finally {
      if (generation === localGeneration) busy.value = false
    }
  }

  window.addEventListener('air-quality:clear-reads', clear)
  onScopeDispose(() => {
    window.removeEventListener('air-quality:clear-reads', clear)
    clear()
  })

  return { data, error, busy, run, clear }
}
