import type { DataScope } from '../stores/session'
import { parseWholeQueryIntent } from './queryContract'

export type Method = 'statistics' | 'compare' | 'pearson'
export type Grain = 'hour' | 'day' | 'month' | 'season'
export type Page = { next_cursor: string | null; page_size: number }
export type Bounds = { start: string; end: string }
export type Support = { support: 'observed' | 'effective' | 'paired'; bucket: Bounds & { local_hour?: number; pos?: number } }
export type Series = {
  product: number; source: string; shape: 'C' | 'S' | 'H'; city?: number; site_epoch?: number
  indicator: string; unit: string; window_definition: string; report_kind: string
  standard_series: string; source_timezone: string; permitted_from: string; permitted_until: string
}
export type SeriesPage = { results: Series[]; page: Page }
export type Selection = Bounds & {
  product: number; source: string; scope: 'formal' | 'synthetic'; cities?: number[]; site_epochs?: number[]
  indicators: string[]; units: Record<string, string>; standard_series: Record<string, string>
  report_kind: string; window_definition: string; quality: 'reliable'
}
export type Parent = { method: Method; method_version: string; grain: Grain; selection: Selection }
export type Coverage = {
  E: number | null; O: number; V: number; received_ratio: number | null; valid_ratio: number | null
  outside_schedule: number; denominator_state: string; exclusions: Record<string, number>
}
export type Column = Coverage & {
  pos: number; entity: number; indicator: string; unit: string; standard_series: string
  expected_windows: Bounds[]; unknown_windows: Bounds[]
}
export type Bucket = Coverage & Bounds & {
  pos: number; conclusion: { state: string }; mean?: number | null; median?: number | null
  minimum?: number | null; maximum?: number | null; minimum_at?: string | null; maximum_at?: string | null
  minimum_ties?: number; maximum_ties?: number
  box?: { n: number; q1: number | null; median: number | null; q3: number | null; lower_whisker: number | null; upper_whisker: number | null }
  complete_month?: boolean; complete_season?: boolean; all_received?: boolean; all_effective?: boolean
  season?: string; season_year?: number; drilldown: Support; observed_drilldown: Support
}
export type ExactPoint = { pos: number; at: string; quality: string; value: number | null; expected: boolean | null; censor_limit: number | null; censor_direction: string }
export type RangePoint = { pos: number; at: string; minimum: number | null; maximum: number | null; valid_count: number; gaps: number }
export type Chart = { resolution: 'exact'; points: ExactPoint[] } | { resolution: 'range'; interval_seconds: number; points: RangePoint[] }
export type Ranking = { pos: number; entity: number; mean: number; rank: number }[]
export type Result = Parent & {
  input_version: string; requested_window: Bounds; source_timezone: string; state: 'ok' | 'no_data'
  columns: Column[]; conclusion: { state: string }; attribution: string[]; chart?: Chart
  buckets?: Bucket[]; months?: Bucket[]; E_common?: number | null; P?: number | null; actual_pairs?: number
  paired_ratio?: number | null; expected_common_windows?: Bounds[]; paired_drilldown?: Support
  pearson?: number | null; ranking?: Ranking | null; mean_difference?: number | null
  partial_summary?: { ranking: Ranking; mean_difference: number | null } | null
  paired_points?: { at: string; difference: number; percent_change: number | null }[]
  whole_period_mean?: number | null; partial_mean?: number | null
  distribution?: { lower: number | null; upper: number | null; n: number }[]
  heatmap?: (Coverage & { month: string; hour: number; mean: number | null; drilldown: Support; observed_drilldown: Support })[]
  anomalies?: { state: string; count: number | null; truncated?: boolean; candidates?: { key_id: number; at: string; value: number; drilldown: Support }[] }
}
export type Detail = Parent & {
  input_version: string; state: 'ok' | 'no_data'; page: Page
  results: { pos: number; key: number; observed_at: string; fetched_at: string | null; value: string | null; quality: string; unit: string; censor_limit: string | null; censor_direction: string }[]
}
export type Saved = { id: string; title: string }
export type Rerun = { state: 'current' | 'data_changed' | 'baseline_unavailable' | 'target_unavailable' | 'method_changed'; result?: Result }

export const methods: Record<Method, { label: string; instruction: string }> = {
  statistics: { label: '趋势与分布', instruction: '选择一个对象的一项指标。' },
  compare: { label: '对象对比', instruction: '按顺序选择 2 至 5 个同层对象的同一指标。同城站点与站址期以服务端校验为准。' },
  pearson: { label: '指标关联', instruction: '依次选择同一对象的两项浓度指标。' },
}
export const grains: Record<Grain, string> = { hour: '小时', day: '日', month: '月', season: '季' }
const day = 86_400_000
const annual = (method: Method, grain: Grain) => method === 'statistics' && (grain === 'month' || grain === 'season')

function parseUtcMinute(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return NaN
  const stamp = Date.parse(`${value}:00Z`)
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 16) === value ? stamp : NaN
}

export function timeWindow(start: string, end: string, method: Method, grain: Grain) {
  const first = parseUtcMinute(start)
  const last = parseUtcMinute(end)
  if (!Number.isFinite(first) || !Number.isFinite(last) || first >= last) return null
  if (last - first > (annual(method, grain) ? 366 : 30) * day) return null
  return { start: new Date(first).toISOString(), end: new Date(last).toISOString() }
}

export function seriesDiscoveryWindow(bounds: Bounds, from = ''): Bounds | null {
  const first = Date.parse(bounds.start)
  const last = Date.parse(bounds.end)
  const start = from ? parseUtcMinute(from) : first
  if (!(first <= start && start < last)) return null
  return {
    start: new Date(start).toISOString(),
    end: new Date(Math.min(last, start + 30 * day)).toISOString(),
  }
}

export function entity(series: Series) {
  return (series.city ?? series.site_epoch)!
}

export function seriesKey(series: Series) {
  return JSON.stringify([series.product, entity(series), series.indicator, series.unit, series.window_definition, series.standard_series])
}

export function seriesLabel(series: Series) {
  const kind = series.city !== undefined ? '城市' : '站址期'
  return `${series.shape} ${kind} #${entity(series)} · ${series.indicator}`
}

export function columnLabel(result: Pick<Result, 'columns' | 'selection'>, pos: number) {
  const column = result.columns.find(item => item.pos === pos)
  if (!column) return '—'
  return `${result.selection.cities ? '城市' : '站址期'} #${column.entity} · ${column.indicator}`
}

export function canAdd(method: Method, chosen: Series[], series: Series) {
  if (chosen.some(item => seriesKey(item) === seriesKey(series))) return true
  if (method === 'pearson' && series.indicator === 'AQI') return false
  if (method === 'compare' && series.standard_series === 'unknown') return false
  const first = chosen[0]
  if (!first) return true
  if (first.product !== series.product || first.source !== series.source || first.shape !== series.shape
    || first.window_definition !== series.window_definition || first.report_kind !== series.report_kind) return false
  if (method === 'compare') return chosen.length < 5 && chosen.every(item => entity(item) !== entity(series))
    && first.indicator === series.indicator && first.unit === series.unit && first.standard_series === series.standard_series
  if (method === 'pearson') return chosen.length < 2 && entity(first) === entity(series) && first.indicator !== series.indicator
  return false
}

export function makeParent(method: Method, grain: Grain, scope: DataScope, bounds: Bounds, series: Series[]): Parent | null {
  const minimum = method === 'statistics' ? 1 : 2
  if (series.length < minimum || series.some((item, i) => !canAdd(method, series.slice(0, i), item))) return null
  if (new Set(series.map(seriesKey)).size !== series.length) return null
  const first = series[0]!
  const ids = [...new Set(series.map(entity))]
  const duration = Date.parse(bounds.end) - Date.parse(bounds.start)
  const long = annual(method, grain)
  const numCells = Math.ceil(duration / 3_600_000) * series.length
  if (!(duration > 0 && duration <= (long ? 366 : 30) * day && numCells <= (long ? 8784 : 3600))) return null
  return {
    method, grain, method_version: 's05.v1',
    selection: {
      start: bounds.start, end: bounds.end, scope: scope || 'formal', product: first.product, source: first.source,
      ...(first.city !== undefined ? { cities: ids } : { site_epochs: ids }),
      indicators: [...new Set(series.map(item => item.indicator))],
      units: Object.fromEntries(series.map(item => [item.indicator, item.unit])),
      standard_series: Object.fromEntries(series.map(item => [item.indicator, item.standard_series])),
      report_kind: first.report_kind, window_definition: first.window_definition, quality: 'reliable',
    },
  }
}

export const parentOf = ({ selection, method, method_version, grain }: Parent): Parent => ({ selection, method, method_version, grain })

export function wholeQueryForResult(result: Result, scope: DataScope) {
  const { selection, method, state, chart } = result
  if (state !== 'ok' || method !== 'statistics' || !chart || selection.quality !== 'reliable' ||
      selection.scope !== (scope || 'formal') || selection.indicators.length !== 1) return null
  const { cities, site_epochs: sites, indicators: [indicator] } = selection
  if ((cities && sites) || (cities ?? sites)?.length !== 1) return null
  const query = {
    intent: 'whole', ...(scope ? { scope } : {}),
    product: String(selection.product), source: selection.source,
    ...(cities ? { city: String(cities[0]) } : { site_epoch: String(sites?.[0]) }),
    indicator, unit: selection.units[indicator], standard_series: selection.standard_series[indicator],
    report_kind: selection.report_kind, window_definition: selection.window_definition,
    start: selection.start, end: selection.end,
  }
  return parseWholeQueryIntent(query, scope) ? query : null
}

export function detailRequest(result: Parent & { input_version: string }, support: Support, cursor?: string) {
  return { ...parentOf(result), input_version: result.input_version, ...support, page_size: 20, ...(cursor ? { cursor } : {}) }
}

export function chartSupport(result: { requested_window: Bounds; columns: { pos: number }[] }, at: string, pos: number, widthSeconds = 3600): Support | null {
  const point = Date.parse(at)
  const windowStart = Date.parse(result.requested_window.start)
  const windowEnd = Date.parse(result.requested_window.end)
  if (![point, windowStart, windowEnd, widthSeconds].every(Number.isFinite) || widthSeconds <= 0) return null
  const start = Math.max(point, windowStart)
  const end = Math.min(point + widthSeconds * 1000, windowEnd)
  if (start >= end || !Number.isInteger(pos) || !result.columns.some(column => column.pos === pos)) return null
  return { support: 'observed', bucket: { start: new Date(start).toISOString(), end: new Date(end).toISOString(), pos } }
}

export function metric(value: number | null | undefined) {
  return value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 7, useGrouping: false }).format(value)
}

export function ratio(value: number | null | undefined) {
  return value == null ? '—' : new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 }).format(value)
}

const formatters = new Map<string, Intl.DateTimeFormat>()
export function stamp(value: string | null | undefined, zone: string) {
  if (!value) return '—'
  let format = formatters.get(zone)
  if (!format) {
    format = new Intl.DateTimeFormat('zh-CN', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'shortOffset' })
    formatters.set(zone, format)
  }
  return format.format(new Date(value))
}

export const conclusionNames: Record<string, string> = {
  denominator_unknown: '预期时槽未知', no_expected_slots: '无预期时槽', sufficient: '覆盖充分',
  partial_coverage: '部分覆盖，仅解释获准区间', insufficient_coverage: '覆盖不足',
  insufficient_pairs: '有效配对少于 30', zero_variance: '至少一列方差为零',
}
export const qualityNames: Record<string, string> = {
  reliable: '有效', valid: '有效', missing: '缺测', censored: '删失', invalid: '无效',
  gap: '未到报', suspect: '可疑', disputed: '争议', unknown: '未知', outside_schedule: '计划外',
}
export function observationValue(row: { value: number | string | null; censor_limit: number | string | null; censor_direction: string; quality: string }) {
  if (row.quality === 'censored' && row.censor_limit != null) return `${row.censor_direction}${row.censor_limit}`
  if (row.value == null) return '—'
  return typeof row.value === 'string' ? row.value : metric(row.value)
}

export const windowNames: Record<string, string> = {
  hourly_label_unspecified: '小时 · 标签位置未说明', hourly_left_label: '小时 · 左标', hourly_right_label: '小时 · 右标',
  rolling_8h_right_label: '滚动 8 小时 · 右标', rolling_24h_right_label: '滚动 24 小时 · 右标',
}
