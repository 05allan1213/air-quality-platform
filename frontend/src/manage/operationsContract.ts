import type { LocationQuery } from 'vue-router'

export const components = {
  web: 'Web 服务', pg: 'PostgreSQL', redis: 'Redis', worker: '采集 Worker', beat: '调度 Beat',
} as const

export type ComponentName = keyof typeof components
export type Proof = {
  state: 'ok' | 'unavailable' | 'unknown'
  observed_at: string | null
  evidence_kind: string
  valid_until: string | null
  reason_code: string
}
export type WorkerProof = Proof & {
  queue_readiness: Proof & { queue: string }
  last_consumption: { observed_at: string; queue: string } | null
}
export type ComponentStates = Record<ComponentName, Proof> & { worker: WorkerProof }

type Coverage = { E: number | null; O: number; V: number }
export type WindowDiagnostics = {
  window_id: number
  state: string
  result: string | null
  coverage: (Coverage & { latest_observed_at: string | null; latest_valid_at: string | null }) | null
  truncated: boolean
  visible_pending: number
  visible_sent_not_started: number
  visible_quota_wait: number
  visible_unconfirmed_dispatch: number
  visible_retry: number
  visible_running: number
  oldest_visible_pending_created_at: string | null
  oldest_visible_pending_age_seconds: number | null
  next_visible_quota_check_at: string | null
  latest_visible_fetched_at: string | null
  first_valid_write_page_truncated: boolean
  first_valid_write_state: 'known' | 'none' | 'unavailable'
  latest_first_valid_write_at: string | null
  visible_errors: { error_code: string; result: string; request_state: string }[]
}
export type OperationsProduct = { product: number; source: string; shape: string; links: { trends: string } }
export type OperationsProductPage = { results: OperationsProduct[]; next_cursor: string | null }

const concentrations = ['PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3']
const cityIndicators = ['AQI', ...concentrations]
const indicators: Record<string, string[]> = {
  L: ['AQI', ...concentrations.map(indicator => `${indicator}Level`)],
  C: cityIndicators, S: cityIndicators, T: ['AQI'], D: cityIndicators, H: concentrations, F: cityIndicators,
}

export function operationsIndicators(shape?: string) {
  return shape && Object.hasOwn(indicators, shape) ? indicators[shape] : []
}

export type Health = { components: ComponentStates; window: WindowDiagnostics | null }
export type WindowOption = {
  id: number
  product: number
  synthetic: boolean
  target_start: string
  target_end: string
  full_scope: boolean
  result: string | null
}
export type WindowPage = {
  results: WindowOption[]
  page: { page_size: number; next_cursor: string | null }
}

type Ratio = { numerator: number; denominator: number | null; ratio: number | null }
type Timing = { sample_count: number; known_count: number; mean_seconds: number | null }
export type Trend = {
  product: number
  synthetic: boolean
  start: string
  end: string
  bucket_timezone: string
  evaluated_at: string
  buckets: {
    date: string
    window_count: number
    partially_authorized_windows: number
    window_results: Record<string, number>
    coverage: Coverage & { unknown_E_windows: number; received: Ratio; valid: Ratio }
    first_observation_window_rate: Ratio
    source_publication_delay: null
    terminal_attempt_states: Record<string, number>
    response_attempt_rate: Ratio
    queue_wait: Timing
    execution: Timing
    schedule_delay: Timing
  }[]
}

export const evidenceNames: Record<string, string> = {
  http_request: 'HTTP 请求', postgres_clock: '数据库时钟', redis_ping: 'Redis PING',
  worker_heartbeat: 'Worker 心跳', scheduler_tick: 'Beat 调度脉冲', worker_subscription: '采集队列订阅',
  probe_failed: '探测未成功', no_evidence: '尚未收到证据', stale_evidence: '证据已过期或时标无效',
  queue_not_subscribed: '未订阅采集队列',
}
export const stateLabel: Record<string, string> = {
  ok: '可用', unavailable: '不可用', unknown: '未知', partially_authorized: '部分可见',
  closed: '已关闭', open: '开放', active: '执行中', disposed: '已处置', not_applicable: '不适用',
  new: '产生新观测', no_new: '未产生新观测', partial: '部分到报', quality_only: '仅质量结果',
  changed: '观测发生变化', failed: '失败', pending: '待处理', finished: '已结束',
  expired: '已过期', coalesced: '已合并', paused: '已暂停',
  responded: '已响应', unsent: '未发送', reserved: '已预留', unknown_after_wire: '发出后状态未知',
}

export const filterKeys = ['product', 'start', 'end', 'object_code', 'indicator'] as const
export type OpsFilters = Record<(typeof filterKeys)[number], string>

export function filtersFromRoute(query: LocationQuery): OpsFilters {
  const value = (key: keyof OpsFilters) => typeof query[key] === 'string' ? query[key] : ''
  return { product: value('product'), start: value('start'), end: value('end'), object_code: value('object_code'), indicator: value('indicator') }
}

function dateMs(value: string) {
  if (!/^(?!000[01]|9999)\d{4}-\d{2}-\d{2}$/.test(value)) return NaN
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date.getTime() : NaN
}

export function periodError(start: string, end: string) {
  const elapsed = dateMs(end) - dateMs(start)
  if (!Number.isFinite(elapsed) || elapsed <= 0) return '请输入有效的起止日期，结束日期不包含在内。'
  if (elapsed > 30 * 86_400_000) return '日期区间最多 30 天。'
  return ''
}

export function positiveId(value: string) {
  return /^[1-9]\d{0,15}$/.test(value) && Number.isSafeInteger(Number(value))
}

export function filterError(filters: OpsFilters) {
  if (!positiveId(filters.product)) return '请从获权目录选择产品。'
  if (filters.object_code.length > 100 || filters.object_code.includes('*')) return '请输入有效的对象代码。'
  return periodError(filters.start, filters.end)
}

export function filterParams(filters: OpsFilters) {
  return Object.fromEntries(filterKeys.filter(key => filters[key]).map(key => [key, filters[key]]))
}

const clockFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
})
export function clockText(at: string | null | undefined) {
  if (!at) return '—'
  const date = new Date(at)
  return Number.isFinite(date.getTime()) ? `${clockFormat.format(date)} UTC` : '—'
}

const secondsFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const percentFormat = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 })
export function secondsText(seconds: number | null) {
  return seconds === null ? '—' : `${secondsFormat.format(seconds)} 秒`
}
export function timingText(timing: Timing) {
  return `${secondsText(timing.mean_seconds)}（已知 ${timing.known_count}/${timing.sample_count}）`
}
export function ratioText(ratio: Ratio) {
  if (ratio.denominator === null) return '—（分母未知）'
  if (ratio.denominator === 0) return '—（分母为 0）'
  return ratio.ratio === null ? '—' : `${percentFormat.format(ratio.ratio)}（${ratio.numerator}/${ratio.denominator}）`
}

export const auditKinds = {
  job: '采集任务', run: '单次运行', quality_issue: '质量事件', station: '站点', grant: '许可',
} as const
export type AuditKind = keyof typeof auditKinds

export function allowedAuditKinds(actions: string[]): AuditKind[] {
  const options: AuditKind[] = []
  if (actions.includes('manage_collection') || actions.includes('configure_collection_schedule')) options.push('job')
  if (actions.includes('review_observation')) {
    options.push('run', 'quality_issue')
    if (actions.includes('manage_catalog')) options.push('station')
  }
  if (actions.includes('manage_grants')) options.push('grant')
  return options
}

export function auditWindow(start: string, end: string) {
  const utc = (value: string) => {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || !Number.isFinite(dateMs(value.slice(0, 10)))) return null
    const stamp = new Date(`${value}:00Z`)
    return Number.isFinite(stamp.getTime()) && stamp.toISOString().slice(0, 16) === value ? stamp : null
  }
  const from = utc(start)
  const until = utc(end)
  if (!from || !until || until.getTime() <= from.getTime() || until.getTime() - from.getTime() > 30 * 86_400_000) return null
  return { start: from.toISOString(), end: until.toISOString() }
}

export function auditObjectId(kind: AuditKind, id: string) {
  if (kind === 'run') return /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id)
  return /^[1-9]\d{0,18}$/.test(id) && BigInt(id) <= 9_223_372_036_854_775_807n
}
