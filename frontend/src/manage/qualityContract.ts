import { ApiError } from '../api/client'

export type IssueStatus = 'open' | 'in_review' | 'resolved'
export type QualityAction = 'claim' | 'hold' | 'close' | 'correct_source' | 'correct_mapping'
export type StationAction = 'rename' | 'relocate' | 'reuse_code'
export type Correction = Extract<QualityAction, 'correct_source' | 'correct_mapping'>

interface Span {
  geography: string
  object_code: string
  indicator: string
  start: string
  end: string
}

export interface Impact extends Span {
  product: number
  synthetic: boolean
  city_id?: number
  site_epoch_id?: number
  observed_at?: string
  window_definition?: string
  standard_series?: string
}

export interface Issue {
  id: number
  event_version: number
  status: IssueStatus
  severity: string
  code: string
  rule_version: number | null
  handler: number | null
  created_at: string
  resolved_at: string | null
  run: string | null
  run_item: number | null
  window: number | null
  batch: number | null
  receipt: number | null
  affected: Impact
  current_revision?: number | null
  candidate_revisions?: number[]
  watermark?: number | null
  qualification_version?: number
  disputed?: boolean
  evidence?: Partial<Record<Correction, string>>
}

export interface IssuePage { results: Issue[]; next_before: number | null }
export interface QualityProductPage {
  results: { product: number; source: string; shape: string }[]
  next_cursor: string | null
}

export function isDayRange(start: string, end: string) {
  const dayNumber = (value: string) => {
    if (!/^(?!000[01]|9999)\d{4}-\d{2}-\d{2}$/.test(value)) return NaN
    const day = new Date(`${value}T00:00:00Z`)
    return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === value ? day.getTime() / 86_400_000 : NaN
  }
  const days = dayNumber(end) - dayNumber(start)
  return days > 0 && days <= 30
}
export interface WindowRow {
  id: number
  product: number
  synthetic: boolean
  target_start: string
  target_end: string
  full_scope: boolean
  result: string | null
}
export interface CursorPage<T> { results: T[]; page: { page_size: number; next_cursor: string | null } }
export interface AuditRow {
  at: string
  action: string
  outcome: string
  actor_relation: 'self' | 'other' | 'system'
  before_version?: number
  after_version?: number
}
export interface Counts { E: number | null; O: number; V: number }
export interface Cell {
  item_key: string
  object_code: string
  observed_at: string
  indicator: string
  received: boolean
  valid: boolean
  reason: string
  expected?: boolean
}
export interface WindowTrace {
  window_id: number
  state: string
  coverage: (Counts & { by_indicator: Record<string, Counts> }) | null
  product?: number
  synthetic?: boolean
  target_start?: string
  target_end?: string
  result?: string | null
  cells?: Cell[]
  next_after?: number | null
  runs?: { id: string; status: string; dispatch: string; result: string | null; error_code: string }[]
  next_run_after?: number | null
  items?: { id: number; run_id: string; item_key: string; batch_id: number | null; status: string; reason: string }[]
  items_truncated?: boolean
  receipts?: { id: number; item_id: number; batch_id: number; outcome: string; revision: { type: 'city' | 'station'; id: number | null } }[]
  receipts_truncated?: boolean
  quality_issues?: Issue[]
}

export interface EvidenceReceipt {
  receipt_id?: number
  key_id?: number
  revision?: Revision
  batch_id?: number
  outcome: string
  locator?: string | null
}

export type EvidenceSelector = { batch: number } | { city_key: number } | { station_key: number }
export interface Revision {
  revision_id: number
  revision_no: number
  value: string | null
  unit: string
  quality: string
  censor_limit: string | null
  censor_direction: string
  rule: number
  current: boolean
  candidate: boolean
  kind: string
  source_token: number | null
  source_time?: string | null
  source_description?: string | null
  raw_value?: string | null
  raw_unit?: string | null
}
export interface EvidencePage {
  attribution: string
  issues: string[]
  revisions?: Revision[]
  receipts: { receipt_id?: number; key_id?: number; revision?: Revision; batch_id?: number; outcome: string; locator?: string | null }[]
  next_after: number | null
  profile?: string
  fetched_at?: string
  disputed?: boolean
  watermark?: number | null
}

export interface QualityReceipt {
  action_id: string
  status: IssueStatus
  event_version: number
  affected?: Impact[]
  outcome?: string
}
export interface StationImpact {
  product: number
  synthetic: boolean
  scope: Span[]
  start: string
  end: string | null
  old_epoch?: number
  new_epoch?: number
}
export interface StationProof { version: number; affected: StationImpact; evidence_ref: string }
export interface StationReceipt { action_id: string; status: 'applied' | 'mapping_review_required'; version: number; affected?: StationImpact }

export const issueStates: Record<IssueStatus, string> = { open: '待审阅', in_review: '审阅中', resolved: '已结案' }
export const actionLabels: Record<QualityAction, string> = {
  claim: '领取审阅', hold: '保留隔离', close: '普通结案', correct_source: '可信来源更正', correct_mapping: '映射更正',
}
export const actionReasons = {
  claim: 'claim_review', hold: 'retain_isolation', close: 'review_complete',
  correct_source: 'verified_correction', correct_mapping: 'registered_mapping',
  rename: 'documented_rename', relocate: 'documented_relocation', reuse_code: 'documented_code_reuse',
} as const

const labels: Record<string, string> = {
  applied: '已记录', active: '运行中', open: '开放', closed: '关闭', disposed: '已处置', not_applicable: '不适用',
  partially_authorized: '部分授权', partial: '部分到报', no_new: '未产生新观测', changed: '有修订',
  new: '产生新观测', quality_only: '仅有质量结果', finished: '已结束',
  insufficient_evidence: '证据不足', not_acquired: '未获取', missing_key: '应到项缺报',
  upstream_not_reported: '上游未报告', invalid_field: '字段无效', disputed: '存在争议',
  retired: '已退役', valid: '有效', missing: '缺失', censored: '删失', rejected: '拒收',
  same_version_conflict: '同版本异值', unordered_conflict: '无序版本冲突', mapping_review_required: '映射待核',
  local_mapping_fix: '已写入映射更正', mapping_conflict: '目标键存在冲突',
  blocking: '阻断', info: '提示', warning: '警告', first: '首次写入',
  content_changed: '内容修订', watermark_advanced: '推进水位', late: '迟到', duplicate: '重复',
}
export function label(value: string | null | undefined, unavailable = '未披露') {
  return value ? Object.hasOwn(labels, value) ? labels[value] : value : unavailable
}

export function timeText(value: string | null | undefined) {
  return value || '未披露'
}

export function numericId(value: unknown): number | undefined {
  if (typeof value !== 'string' || !/^\d{1,16}$/.test(value)) return
  const n = Number(value)
  return Number.isSafeInteger(n) && n > 0 ? n : undefined
}

export function runId(value: unknown): string | undefined {
  return typeof value === 'string' && /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(value) ? value : undefined
}

export function queryInteger(value: unknown) {
  if (typeof value === 'number') return value
  return numericId(value) ?? (typeof value === 'string' && value === '0' ? 0 : undefined)
}

export function requestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const raw = crypto.getRandomValues(new Uint8Array(16))
  raw[6] = (raw[6] & 15) | 64
  raw[8] = (raw[8] & 63) | 128
  const h = [...raw].map(i => i.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

export function denied(cause: unknown) {
  return cause instanceof ApiError && (cause.status === 403 || cause.status === 404)
}
