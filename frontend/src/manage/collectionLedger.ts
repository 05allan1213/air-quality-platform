import { ApiError } from '../api/client'

export type Scope = '' | 'synthetic'
export type Purpose = 'ledger' | 'execute'
export type RunReason = 'manual' | 'review' | 'backfill'
export type JobAction = 'pause' | 'resume' | 'resume_source' | 'resume_product'

export interface CursorPage<T> {
  results: T[]
  page?: { next_cursor: string | null }
  next_cursor?: string | null
}

export interface DirectoryEntry {
  product: number
  source: string
  shape: string
  links: Record<string, string>
}

export interface ExecutionEntry {
  product: number
  job: number
  reason: RunReason
  target: string
}

export interface JobRow {
  id: number
  product: number
  synthetic: boolean
  profile: string
  state: string
  links: Record<string, string>
}

export interface JobDiagnostic {
  job: number
  targets: Record<'job' | 'source' | 'product', ControlTarget>
}

export interface ControlTarget {
  type: 'job' | 'source' | 'product' | 'run'
  id: number | string
  control_version: number
  state: string
  request_state?: string
  dispatch?: string
  owner_present?: boolean
  reason?: string
}

export interface WindowRow {
  id: number
  product: number
  synthetic: boolean
  target_start: string
  target_end: string
  full_scope: boolean
  result: string | null
  links: Record<string, string>
}

export interface CoverageCounts {
  E: number | null
  O: number
  V: number
  by_indicator?: Record<string, CoverageCounts>
  [key: string]: unknown
}

export interface WindowCell {
  item_key: string
  object_code: string
  observed_at: string
  indicator: string
  received: boolean
  accepted: boolean
  valid: boolean
  reason: string
  expected?: boolean
}

export interface RunEntry {
  id: string
  reason: string
  status: string
  attempt: number
  result: string | null
  error_code: string
  dispatch: string
  deliveries: number
  request_state: string
  created_at: string
  batch_id: number | null
  next_retry_at: string | null
}

export interface RunItem {
  id: number
  run_id: string
  item_key: string
  batch_id: number | null
  status: string
  accepted: boolean
  reason: string
}

export interface RunReceipt {
  id: number
  item_id: number
  batch_id: number
  outcome: string
  revision: { type: 'city' | 'station'; id: number | null }
  metadata?: Record<string, unknown>
}

export interface QualityIssue {
  id: number
  status: string
  severity: string
  code: string
  window: number | null
  run: string | null
}

export interface CollectionWindowProjection {
  window_id: number
  product?: number
  synthetic?: boolean
  target_start?: string
  target_end?: string
  state: string
  result?: string | null
  coverage: CoverageCounts | null
  cells?: WindowCell[]
  next_after?: number | null
  runs?: RunEntry[]
  next_run_after?: number | null
  items?: RunItem[]
  items_truncated?: boolean
  receipts?: RunReceipt[]
  receipts_truncated?: boolean
  quality_issues?: QualityIssue[]
  target?: ControlTarget
}

export interface HistoryAttempt {
  id: string
  state: string
  dispatch: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  links: Record<string, string>
}

export interface RunCommand {
  job: number
  target: string
  reason: RunReason
  request_id: string
}

export interface SubmissionReceipt {
  run_id: string
  window_id: number
  dispatch?: string
  status?: string
}

export type CollectionCommandLookup =
  | { family: 'manual_run'; target: number; command: RunCommand }
  | { family: 'job_control'; target: number; command: JobControlCommand }
  | { family: 'redeliver'; target: string; command: RedeliveryCommand }

export interface GovernanceReceipt {
  state: string
  action: string
  recorded_at: string
  action_id: string
  version: number
}

export interface HistorySubmission {
  attempt_id: string
}

export interface QualityContext {
  product: number
  window: number
  run?: string
  issue?: number
  batch?: number
}

export interface JobControlCommand {
  action: JobAction
  request_id: string
  reason_code: string
  expected_version: number
}

export interface RedeliveryCommand {
  action: 'redeliver'
  request_id: string
  reason_code: 'operator_recovery'
  expected_version: number
}

export interface HistoryImportCommand {
  profile: 'uci501-edges-v1'
  stage_id: string
  reason: 'historical_verification'
}

export function nextCursor<T>(page: CursorPage<T> | null | undefined) {
  return page?.page?.next_cursor ?? page?.next_cursor ?? null
}

export function ledgerPath(href: string | undefined, expected: string) {
  const prefix = `/api/v1/${expected}`
  if (!href || (href !== prefix && !href.startsWith(`${prefix}?`))) return undefined
  return href.slice('/api/v1/'.length)
}

export function createRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function buildRunCommand(job: number, target: string, reason: RunReason): RunCommand {
  return { job, target, reason, request_id: createRequestId() }
}

export function buildJobControlCommand(
  target: ControlTarget,
  action: JobAction,
): JobControlCommand {
  return {
    action,
    request_id: createRequestId(),
    reason_code: action === 'pause' ? 'operator_pause' : action === 'resume' ? 'operator_resume' : 'operator_recovery',
    expected_version: target.control_version,
  }
}

export function buildRedeliveryCommand(target: ControlTarget): RedeliveryCommand {
  return {
    action: 'redeliver',
    request_id: createRequestId(),
    reason_code: 'operator_recovery',
    expected_version: target.control_version,
  }
}

export function buildHistoryImportCommand(stageId: string): HistoryImportCommand {
  return { profile: 'uci501-edges-v1', stage_id: stageId, reason: 'historical_verification' }
}

export function isUuid(value: string) {
  return /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(value)
}

export function errorText(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback
  if (error.code === 'page_changed') return '授权目录或结果已变化，请从第一页重新读取。'
  if (error.code === 'csrf_failed') return '安全校验已更新，操作未自动重放；请重新读取后确认再提交。'
  if (error.code === 'precondition_conflict' || error.status === 409) return '命令与当前状态或版本冲突，未重放操作；请重新读取当前状态。'
  if (error.status === 403 || error.status === 404) return '当前会话无法访问此范围，已隐藏相关数据。'
  return error.message || fallback
}

export function label(value: string | null | undefined) {
  if (value == null || value === '') return '无'
  const known: Record<string, string> = {
    active: '活动', paused: '已暂停', open: '开放', closed: '已关闭', disposed: '已处置',
    partially_authorized: '部分授权', pending: '等待中', running: '执行中', finished: '已结束',
    failed: '失败', expired: '已过期', coalesced: '已合并', unsent: '未请求', sent: '已投递',
    new: '新增观测', changed: '发生变化', no_new: '未产生新观测', quality_only: '仅有质量结果',
    partial: '部分完成', responded: '已响应', absent: '缺项', accepted: '已接受', rejected: '已拒绝',
    valid: '有效', invalid: '无效', missing_key: '缺少键', insufficient_evidence: '证据不足',
    upstream_transient: '来源暂时不可用', database_error: '数据库错误', timeout: '超时',
    manual: '手动', review: '复核', backfill: '补采', retry: '重试', in_review: '审阅中',
    resolved: '已解决', critical: '严重', warning: '警告', info: '提示', possible: '可能已请求',
    unconfirmed: '投递未确认', operator_pause: '操作者暂停', scheduled: '定时',
    first: '首次写入', duplicate: '重复', content_changed: '内容修订', watermark_advanced: '水位推进',
    succeeded: '已完成', not_applicable: '不适用',
  }
  return Object.hasOwn(known, value) ? known[value] : value
}

export function todayUtc() {
  return new Date().toISOString().slice(0, 10)
}

export function addUtcDays(day: string, count: number) {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + count)
  return date.toISOString().slice(0, 10)
}

export function validDateSpan(start: string, end: string) {
  const parse = (day: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || +day.slice(0, 4) < 2 || +day.slice(0, 4) > 9998) return NaN
    const date = new Date(`${day}T00:00:00Z`)
    return Number.isFinite(+date) && date.toISOString().slice(0, 10) === day ? +date : NaN
  }
  const days = (parse(end) - parse(start)) / 86_400_000
  return days > 0 && days <= 30
}

export function validTarget(value: string) {
  const syntax = /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d(?::00(?:\.0{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/
  const day = syntax.exec(value)?.[1]
  if (!day) return false
  const localDate = new Date(`${day}T00:00:00Z`)
  const instant = new Date(value)
  return Number.isFinite(+localDate) && localDate.toISOString().slice(0, 10) === day
    && Number.isFinite(+instant) && instant.getUTCFullYear() >= 2 && instant.getUTCFullYear() <= 9998
    && instant.getUTCMinutes() === 0
}

export function knownCollectionRejection(error: unknown) {
  return error instanceof ApiError && [400, 403, 404, 409, 429].includes(error.status)
}

export function validSubmissionReceipt(receipt: SubmissionReceipt, expectedRun?: string) {
  return Boolean(receipt && typeof receipt.run_id === 'string' && isUuid(receipt.run_id)
    && Number.isSafeInteger(receipt.window_id) && receipt.window_id > 0
    && (!expectedRun || receipt.run_id === expectedRun))
}


export function nextUtcHour() {
  const date = new Date()
  date.setUTCMinutes(0, 0, 0)
  date.setUTCHours(date.getUTCHours() + 1)
  return date.toISOString()
}
