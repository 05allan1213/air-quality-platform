import { localApiHref } from '../monitor/reads'
import type { PageInfo } from '../monitor/reads'
import type { DataScope } from '../stores/session'

export type QueryMode = 'window' | 'latest_valid' | 'detail'
export type CatalogObject = {
  product: number
  shape: 'L' | 'C' | 'S' | 'T' | 'D' | 'H' | 'F'
  source: string
  synthetic: boolean
  city?: number
  site_epoch?: number
  name: string | null
  attribution: string
  rights_attributions: string[]
  observed_at: string | null
  local_report_date: string | null
  indicator: string
  history_link: string
}
export type CatalogPage = { results: CatalogObject[]; page: PageInfo }

export const windows = {
  hourly_label_unspecified: { report: 'hourly', label: '小时 · 标签位置未知' },
  hourly_left_label: { report: 'hourly', label: '小时 · 起点标记' },
  hourly_right_label: { report: 'hourly', label: '小时 · 终点标记' },
  rolling_8h_right_label: { report: 'rolling_8h', label: '滚动 8 小时 · 终点标记' },
  rolling_24h_right_label: { report: 'rolling_24h', label: '滚动 24 小时 · 终点标记' },
  local_daily: { report: 'daily', label: '源地方日报' },
} as const
export type ObservationWindow = keyof typeof windows
const hourly = ['hourly_label_unspecified', 'hourly_left_label', 'hourly_right_label'] as const
const concentrations = ['PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3']
export function allowedWindows(shape: CatalogObject['shape']): readonly ObservationWindow[] {
  if (shape === 'D') return ['local_daily']
  if (shape === 'H') return ['hourly_label_unspecified']
  return ['C', 'S'].includes(shape) ? [...hourly, 'rolling_8h_right_label', 'rolling_24h_right_label'] : hourly
}
export function allowedIndicators(shape: CatalogObject['shape'], window: ObservationWindow): string[] {
  if (windows[window].report.startsWith('rolling')) return ['O3']
  if (shape === 'L') return ['AQI', ...concentrations.map(indicator => `${indicator}Level`)]
  if (shape === 'T') return ['AQI']
  if (shape === 'H') return concentrations
  return ['AQI', ...concentrations]
}
export function unitFor(indicator: string) {
  if (indicator === 'AQI') return '1'
  if (indicator.endsWith('Level')) return 'grade'
  return indicator === 'CO' ? 'mg/m³' : 'µg/m³'
}
export function indexed(indicator: string) {
  return ['1', 'grade'].includes(unitFor(indicator))
}

export const qualities = {
  valid: '有效', censored: '删失', missing: '缺测', suspect: '可疑', invalid: '无效', disputed: '争议', all: '全部质量',
} as const
export type QueryQuality = keyof typeof qualities
export type ObservationRow = {
  city?: number
  site_epoch?: number
  entity?: number
  key?: number
  revision?: number
  revision_id?: number
  input_version?: string
  product: number
  source: string
  indicator: string
  observed_at: string | null
  local_report_date?: string | null
  source_time?: string | null
  source_timezone?: string | null
  source_local_date?: string | null
  source_description?: string | null
  execution_started_at?: string | null
  fetched_at?: string | null
  fetched_at_basis?: string
  source_status?: string
  source_event_at?: string | null
  evaluated_at?: string | null
  value: string | null
  raw_unit?: string | null
  unit: string
  standard_series: string
  window_definition: ObservationWindow
  quality: Exclude<QueryQuality, 'all'>
  censor_direction: string
  censor_limit: string | null
  attribution?: string
  rights_attributions?: string[]
}
export type ObservationPage = {
  state: 'ok' | 'no_data' | 'no_coverage'
  page: PageInfo
  results: ObservationRow[]
  evaluated_at: string
  selection: { cities?: number[]; site_epochs?: number[]; indicators: string[]; as_of?: string }
}
export type ExactResponse = { count: number; reason: string | null; results: ObservationRow[]; attribution: string; rights_attributions: string[] }
export type DetailParent = {
  method: 'detail'
  method_version: 's05.v1'
  grain: 'hour' | 'day'
  selection: {
    product: number
    source: string
    scope: 'formal' | 'synthetic'
    cities?: number[]
    site_epochs?: number[]
    indicators: string[]
    units: Record<string, string>
    standard_series: Record<string, string>
    quality: 'reliable'
    report_kind: string
    window_definition: ObservationWindow
    start: string
    end: string
  }
}
export type DetailPage = DetailParent & {
  input_version: string
  source_timezone: string
  attribution: string[]
  state: 'ok' | 'no_data'
  results: ObservationRow[]
  page: PageInfo
}
export type ExportTicket = { id: string; kind: 'detail'; created_at: string; expires_at: string }
export type ExportResponse = ExportTicket | { state: 'no_data' }
export const detailPageSize = 2
export const maxQueryHours = 720

type WholeQueryIntent = {
  product: number; source: string; object: number; entity: 'city' | 'site_epoch'
  window: ObservationWindow; indicator: string; standard: string
  start: string; end: string
}

const wholeQueryKeys = new Set(['intent', 'scope', 'product', 'source', 'city', 'site_epoch',
  'indicator', 'unit', 'standard_series', 'report_kind', 'window_definition', 'start', 'end'])
const safeId = (value: unknown) => typeof value === 'string' && /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value))

export function parseWholeQueryIntent(query: Record<string, unknown>, scope: DataScope): WholeQueryIntent | null {
  if (query.intent !== 'whole' || query.scope !== (scope || undefined) ||
      Object.keys(query).some(key => !wholeQueryKeys.has(key))) return null
  const { product, source, city, site_epoch: site, indicator, unit, standard_series: standard,
    report_kind: report, window_definition: window } = query
  if ((city === undefined) === (site === undefined)) return null
  const entity = city === undefined ? 'site_epoch' : 'city'
  const object = query[entity]
  if (!safeId(product) || !safeId(object) || typeof source !== 'string' || !source ||
      typeof indicator !== 'string' || !indicator || unit !== unitFor(indicator) || typeof standard !== 'string' ||
      (indexed(indicator) ? !['HJ633-2012', 'HJ633-2026', 'unknown'].includes(standard) : standard !== 'not_applicable')) return null
  if (typeof window !== 'string' || !Object.hasOwn(windows, window) || window === 'local_daily' ||
      windows[window as ObservationWindow].report !== report ||
      typeof query.start !== 'string' || typeof query.end !== 'string') return null
  const start = normalizedInstant(query.start)
  const end = normalizedInstant(query.end)
  if (!start || !end) return null
  const duration = Date.parse(end) - Date.parse(start)
  if (!(duration > 0 && duration <= maxQueryHours * 3_600_000)) return null
  return { product: Number(product), source, object: Number(object), entity,
    window: window as ObservationWindow, indicator, standard, start, end }
}

export function matchesWholeQueryObject(intent: WholeQueryIntent, object: CatalogObject) {
  return object.product === intent.product && object.source === intent.source && object[intent.entity] === intent.object &&
    allowedWindows(object.shape).includes(intent.window) && allowedIndicators(object.shape, intent.window).includes(intent.indicator)
}

export const csvFields = ['qualification_version', 'report_kind', 'fetched_at_basis'] as const
export type CsvField = typeof csvFields[number]
export const csvFieldLabels: Record<CsvField, string> = {
  qualification_version: '判定版本', report_kind: '报告类型', fetched_at_basis: '采集时刻依据',
}

export function rowValue(row: ObservationRow) {
  if (row.quality === 'censored' && row.censor_limit !== null) return `${row.censor_direction}${row.censor_limit}`
  return row.value ?? '—'
}

export function objectId(object: CatalogObject) {
  return object.site_epoch ?? object.city
}
export function objectKey(object: CatalogObject) {
  return `${object.product}:${objectId(object)}`
}
export function objectName(object: CatalogObject) {
  return object.name || `${object.site_epoch ? '站址期' : '城市'} #${objectId(object)}`
}

export function catalogAnchor(object: CatalogObject, scope: DataScope) {
  const link = localApiHref(object.history_link)
  const exact = object.shape === 'D'
  const window = link?.params.window_definition as ObservationWindow | undefined
  if (!link || link.path !== (exact ? 'observations/current/' : 'observations/query/') || !window ||
      !allowedWindows(object.shape).includes(window) || link.params.report_kind !== windows[window].report ||
      link.params.product !== String(object.product) || (link.params.scope || '') !== scope ||
      (exact ? link.params.city : link.params[object.site_epoch ? 'site_epochs' : 'cities']) !== String(objectId(object)) ||
      (exact ? link.params.indicator : link.params.indicators) !== object.indicator) return null
  return { window, start: link.params.start || '', end: link.params.end || '', date: link.params.local_report_date || '', standard: link.params.standard_series || '' }
}

// An explicit offset avoids treating a browser's local clock as the publisher's time zone.
export function normalizedInstant(value: string): string | null {
  const match = /^(\d{4}-\d\d-\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.exec(value.trim())
  if (!match || !validDate(match[1]) || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return null
  return Number.isFinite(Date.parse(value.trim())) ? value.trim() : null
}
export function validDate(value: string) {
  if (!/^\d{4}-\d\d-\d\d$/.test(value) || value.startsWith('0000-')) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

export function canonicalDetail(page: DetailPage, applied: DetailParent): DetailParent | null {
  const { selection, method, method_version, grain, input_version } = page
  const expected = applied.selection
  const matchingList = <T>(left: T[], right: T[]) => [...left].sort().join(',') === [...right].sort().join(',')
  if (method !== 'detail' || method_version !== applied.method_version || grain !== applied.grain ||
      !/^[a-f0-9]{64}$/.test(input_version) || selection.product !== expected.product ||
      selection.scope !== expected.scope || selection.source !== expected.source || selection.quality !== 'reliable' ||
      selection.window_definition !== expected.window_definition || selection.report_kind !== expected.report_kind ||
      Date.parse(selection.start) !== Date.parse(expected.start) || Date.parse(selection.end) !== Date.parse(expected.end) ||
      !matchingList(selection.cities ?? [], expected.cities ?? []) ||
      !matchingList(selection.site_epochs ?? [], expected.site_epochs ?? []) ||
      !matchingList(selection.indicators, expected.indicators) ||
      !selection.indicators.every(indicator => selection.units[indicator] === expected.units[indicator] &&
        selection.standard_series[indicator] === expected.standard_series[indicator])) return null
  return { selection, method, method_version, grain }
}
