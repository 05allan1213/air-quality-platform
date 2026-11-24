// F-only adapter for the representative S07 research shapes. Generated statistics
// are fixture inputs here. The browser only projects and formats them.
import fixtures from './research-fixtures.json'

export const indicators = ['PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3'] as const
export type Indicator = (typeof indicators)[number]
export const productIds = { C: 301, S: 302 } as const
export type Product = keyof typeof productIds
export const source = 'preview.f'
export const sourceTimezone = 'Asia/Shanghai'
export const objects = {
  C: [{ id: 101, label: '云汀市' }, { id: 102, label: '雾川市' }],
  S: [{ id: 201, label: '江湾站' }, { id: 202, label: '北岸站' }, { id: 203, label: '湖西站' }],
} as const
export const modeNames = { statistics: '趋势与分布', compare: '对象对比', pearson: '指标关联' } as const
export type Method = keyof typeof modeNames
export type Grain = 'hour' | 'day'
export const windows = {
  normal: { label: '5 月 16 日 · 24 小时', date: '2024-05-16' },
  extended: { label: '5 月 15–16 日 · 48 小时', date: '2024-05-15 至 05-16' },
  insufficient: { label: '5 月 14 日 · 24 小时', date: '2024-05-14' },
  empty: { label: '5 月 17 日 · 24 小时', date: '2024-05-17' },
} as const
export type WindowKey = keyof typeof windows
export interface Conditions {
  method: Method
  product: Product
  objectIds: number[]
  indicator: Indicator
  other: Indicator
  window: WindowKey
  grain: Grain
}
export type Support = 'observed' | 'effective' | 'paired'
export type Conclusion = 'sufficient' | 'insufficient_coverage' | 'insufficient_pairs' | 'zero_variance'
export type Quality = 'valid' | 'gap' | 'missing' | 'censored'
interface Bounds { start: string; end: string }
interface Coverage {
  E: number
  O: number
  V: number
  received_ratio: number
  valid_ratio: number
  denominator_state: 'known'
  outside_schedule: number
  exclusions: { gap: number; missing: number; censored: number; invalid: number; suspect: number; disputed: number; unknown: number }
}
interface Bucket extends Coverage, Bounds { mean: number | null }
interface ColumnFixture extends Coverage {
  values: (number | null | 'missing' | 'censored')[]
  mean: number | null
  conclusion: { state: Conclusion }
  distribution: { lower: number | null; upper: number | null; n: number }[]
  daily: Bucket[]
}
interface Ranking { pos: number; entity: number; mean: number; rank: number }
interface Pair {
  slots: number[]
  E_common: number
  P: number
  actual_pairs: number
  paired_ratio: number
  conclusion: { state: Conclusion }
  pearson?: number | null
  ranking?: Ranking[] | null
  mean_difference?: number | null
  partial_summary?: null
  interpretation?: string
}
interface FixtureWindow extends Bounds {
  hours: number
  slot_offset: number
  columns: Record<string, ColumnFixture>
  pairs: Record<string, Pair>
  version_seeds: Record<string, string>
}
const fixtureWindows: Record<WindowKey, FixtureWindow> = fixtures as Record<WindowKey, FixtureWindow>

interface Selection extends Bounds {
  product: number
  source: typeof source
  scope: 'synthetic'
  cities?: number[]
  site_epochs?: number[]
  indicators: Indicator[]
  units: Partial<Record<Indicator, string>>
  standard_series: Partial<Record<Indicator, 'not_applicable'>>
  report_kind: 'hourly'
  window_definition: 'hourly_label_unspecified'
  quality: 'reliable'
}
export interface Intent {
  selection: Selection
  method: Method
  method_version: 's05.v1'
  grain: Grain
}
interface Metadata {
  state: 'ok' | 'no_data'
  input_version: string
  source_timezone: typeof sourceTimezone
  attribution: string[]
  requested_window: Bounds
  calendar_versions: string[]
  label_position: 'unspecified'
  timezone_basis: 'product_registration'
}
interface Position {
  pos: number
  entity: number
  indicator: Indicator
  unit: string
  standard_series: 'not_applicable'
}
export interface DetailRow extends Position {
  observed_at: string
  key: number
  revision: number
  qualification_version: number
  quality: Exclude<Quality, 'gap'>
  value: string | null
  censor_limit: string | null
  censor_direction: '<' | null
  source: typeof source
  synthetic: true
  product: number
  source_timezone: typeof sourceTimezone
  window_definition: 'hourly_label_unspecified'
  report_kind: 'hourly'
  fetched_at: string
  fetched_at_basis: 'contributing_batch'
  attribution: string
}
interface Point {
  pos: number
  at: string
  quality: Quality
  value: number | null
  expected: true
  key_id: number | null
  censor_limit: number | null
  censor_direction: '<' | null
}
export interface ResearchReport extends Intent, Metadata {
  columns: (Position & Coverage & { expected_windows: Bounds[]; unknown_windows: Bounds[] })[]
  expected_windows: Bounds[][]
  conclusion: { state: Conclusion }
  chart: { resolution: 'exact'; points: Point[] }
  buckets?: (Bucket & { pos: number })[]
  distribution?: ColumnFixture['distribution']
  whole_period_mean?: number | null
  E_common?: number
  P?: number
  actual_pairs?: number
  paired_ratio?: number
  expected_common_windows?: Bounds[]
  paired_drilldown?: { support: 'paired'; bucket: Bounds }
  pearson?: number | null
  ranking?: Ranking[] | null
  mean_difference?: number | null
  partial_summary?: null
  interpretation?: string
}
interface ResearchDetails extends Intent, Metadata {
  results: DetailRow[]
  page: { next_cursor: string | null; page_size: number }
}
export interface Drilldown {
  support: Support
  bucket?: Bounds & { pos?: number; local_hour?: number }
}
interface DetailRequest extends Drilldown {
  input_version: string
  page_size?: number
  cursor?: string
}

type ErrorCode = 'invalid_request' | 'query_too_large' | 'incompatible_series' | 'input_changed' | 'use_denied' | 'not_found' | 'expired' | 'database_unavailable'
export class MockError extends Error {
  constructor(readonly code: ErrorCode, readonly status: number) { super(code) }
}
export const scenarios = { normal: '标准样本', error: '读取失败', changed: '研究输入已变更', denied: '导出未获许可' } as const
export type Scenario = keyof typeof scenarios
export const defaultConditions = (method: Method = 'compare', fromCity = false): Conditions => ({
  method,
  product: fromCity ? 'C' : 'S',
  objectIds: method === 'compare' ? (fromCity ? [101, 102] : [201, 202]) : [fromCity ? 101 : 201],
  indicator: 'PM2.5',
  other: 'PM10',
  window: method === 'pearson' ? 'extended' : 'normal',
  grain: 'hour',
})
export const unit = (indicator: Indicator) => indicator === 'CO' ? 'mg/m³' : 'µg/m³'
export const objectName = (id: number): string => [...objects.C, ...objects.S].find((item) => item.id === id)?.label ?? String(id)
export const productName = (product: Product) => product === 'S' ? '站点小时观测' : '城市小时观测'
export const productCode = (selection: Selection): Product => selection.product === productIds.C ? 'C' : 'S'
export const attribution = '澄空观测 · 原型合成样本 F'
const hour = 3_600_000
const hex = /^[a-f0-9]{64}$/

export function intentFor(conditions: Conditions): Intent {
  const { method, product, grain } = conditions
  // Catalog order is stable so series and fixture identity survive edits to the selection.
  const objectIds = [...conditions.objectIds].sort((a, b) => a - b)
  if (objectIds.length > 5) throw new MockError('query_too_large', 400)
  if ((method === 'compare' ? objectIds.length < 2 : objectIds.length !== 1)
    || new Set(objectIds).size !== objectIds.length || (method === 'pearson' && conditions.indicator === conditions.other)) {
    throw new MockError('invalid_request', 400)
  }
  if (!objectIds.every((id) => objects[product].some((item) => item.id === id))) throw new MockError('incompatible_series', 422)
  const inds = method === 'pearson' ? [conditions.indicator, conditions.other] : [conditions.indicator]
  const { start, end } = fixtureWindows[conditions.window]
  return {
    method, method_version: 's05.v1', grain,
    selection: {
      product: productIds[product], source, scope: 'synthetic',
      ...(product === 'C' ? { cities: objectIds } : { site_epochs: objectIds }),
      indicators: inds,
      units: Object.fromEntries(inds.map((ind) => [ind, unit(ind)])),
      standard_series: Object.fromEntries(inds.map((ind) => [ind, 'not_applicable'])),
      start, end, quality: 'reliable', report_kind: 'hourly', window_definition: 'hourly_label_unspecified',
    },
  }
}

function fromIntent(intent: Intent, changed = false) {
  const { selection } = intent
  const window = Object.values(fixtureWindows).find((sample) => sample.start === selection.start && sample.end === selection.end)
  const ids = selection.cities ?? selection.site_epochs ?? []
  const positions = ids.flatMap((entity) => selection.indicators.map((indicator) => ({ entity, indicator })))
    .map((item, pos): Position => ({ ...item, pos, unit: unit(item.indicator), standard_series: 'not_applicable' }))
  const keys = positions.map(({ entity, indicator }) => `${entity}:${indicator}`)
  const key = keys.join('|')
  const seed = window?.version_seeds[key]
  if (!window || !seed || selection.scope !== 'synthetic' || selection.source !== source) throw new MockError('invalid_request', 400)
  // Each mock token binds the fixture snapshot, granularity and revision scenario.
  const version = `${changed ? 'b' : 'a'}${intent.grain === 'hour' ? '1' : '2'}${seed}`
  return { window, keys, positions, version, pair: window.pairs[key] }
}

export function reportFor(conditions: Conditions, scenario: Scenario = 'normal'): ResearchReport {
  if (scenario === 'error') throw new MockError('database_unavailable', 503)
  const intent = intentFor(conditions)
  const { window, keys, positions, version, pair } = fromIntent(intent, scenario === 'changed')
  const requested_window = { start: window.start, end: window.end }
  const times = Array.from({ length: window.hours }, (_, slot) => new Date(Date.parse(window.start) + hour * slot).toISOString())
  const columns = positions.map((position) => {
    const { E, O, V, received_ratio, valid_ratio, denominator_state, outside_schedule, exclusions } = window.columns[keys[position.pos]]
    return { ...position, E, O, V, received_ratio, valid_ratio, denominator_state, outside_schedule, exclusions, expected_windows: [requested_window], unknown_windows: [] }
  })
  const points = times.flatMap((at, slot) => positions.map(({ pos, entity, indicator }): Point => {
    const observed = window.columns[keys[pos]].values[slot]
    const quality = observed === null ? 'gap' : typeof observed === 'number' ? 'valid' : observed
    return {
      pos, at, quality, expected: true, value: typeof observed === 'number' ? observed : null,
      key_id: observed === null ? null : entity * 10000 + indicators.indexOf(indicator) * 100 + window.slot_offset + slot + 1,
      censor_limit: quality === 'censored' ? 3 : null, censor_direction: quality === 'censored' ? '<' : null,
    }
  }))
  const first = window.columns[keys[0]]
  const conclusion = pair?.conclusion ?? first.conclusion
  const report: ResearchReport = {
    ...intent, state: columns.some((column) => column.O) ? 'ok' : 'no_data', input_version: version,
    source_timezone: sourceTimezone, attribution: [attribution], requested_window,
    calendar_versions: ['s05-fixture-v1'], timezone_basis: 'product_registration', label_position: 'unspecified',
    columns, expected_windows: columns.map(({ expected_windows }) => expected_windows), chart: { resolution: 'exact', points }, conclusion,
  }
  if (intent.method !== 'pearson') {
    if (intent.grain === 'day') {
      report.buckets = keys.flatMap((column, pos) => window.columns[column].daily.map((bucket) => ({ ...bucket, pos })))
        .sort((a, b) => a.start.localeCompare(b.start) || a.pos - b.pos)
    }
  }
  if (intent.method === 'statistics') {
    report.distribution = first.distribution
    report.whole_period_mean = conclusion.state === 'sufficient' ? first.mean : null
  }
  if (pair) {
    const { E_common, P, actual_pairs, paired_ratio, ranking, mean_difference, pearson, partial_summary, interpretation } = pair
    Object.assign(report, { E_common, P, actual_pairs, paired_ratio, expected_common_windows: [requested_window], paired_drilldown: { support: 'paired', bucket: requested_window } })
    if (intent.method === 'pearson') Object.assign(report, { pearson, interpretation })
    else Object.assign(report, { ranking, mean_difference, partial_summary })
  }
  return report
}

function rowsFor(report: ResearchReport, { support, bucket }: Drilldown): DetailRow[] {
  const { window, pair } = fromIntent(report)
  const lower = bucket ? Date.parse(bucket.start) : Date.parse(window.start)
  const upper = bucket ? Date.parse(bucket.end) : Date.parse(window.end)
  if (!(Date.parse(window.start) <= lower && lower < upper && upper <= Date.parse(window.end))
    || (bucket?.pos !== undefined && (support === 'paired' || !report.columns[bucket.pos]))
    || (bucket?.local_hour !== undefined && !(Number.isInteger(bucket.local_hour) && bucket.local_hour >= 0 && bucket.local_hour < 24))) {
    throw new MockError('invalid_request', 400)
  }
  const paired = new Set(pair?.slots ?? [])
  return report.chart.points.flatMap((point): DetailRow[] => {
    const time = Date.parse(point.at)
    const slot = (time - Date.parse(window.start)) / hour
    if (point.key_id === null || point.quality === 'gap' || time < lower || time >= upper
      || (bucket?.pos !== undefined && bucket.pos !== point.pos)
      || (bucket?.local_hour !== undefined && bucket.local_hour !== slot % 24)
      || (support === 'paired' && !paired.has(slot))
      || (support === 'effective' && point.quality !== 'valid')) return []
    const { pos, entity, indicator, unit, standard_series } = report.columns[point.pos]
    return [{
      pos, entity, indicator, unit, standard_series, observed_at: point.at,
      quality: point.quality, value: point.value === null ? null : point.value.toFixed(6),
      censor_limit: point.censor_limit?.toFixed(6) ?? null, censor_direction: point.censor_direction,
      key: point.key_id, revision: point.key_id + 10_000_000, qualification_version: 1,
      source, product: report.selection.product, synthetic: true, source_timezone: sourceTimezone,
      window_definition: 'hourly_label_unspecified', report_kind: 'hourly',
      fetched_at: new Date(time + 9 * 60_000).toISOString(), fetched_at_basis: 'contributing_batch', attribution,
    }]
  })
}

export function detailsFor(report: ResearchReport, request: DetailRequest, scenario: Scenario = 'normal'): ResearchDetails {
  if (scenario === 'error') throw new MockError('database_unavailable', 503)
  if (!hex.test(request.input_version) || request.input_version !== report.input_version
    || request.input_version !== fromIntent(report, scenario === 'changed').version) throw new MockError('input_changed', 409)
  const page_size = request.page_size ?? 50
  if (!Number.isInteger(page_size) || page_size < 1) throw new MockError('invalid_request', 400)
  if (page_size > 100) throw new MockError('query_too_large', 400)
  const rows = rowsFor(report, request)
  const binding = JSON.stringify([report.input_version, request.support, request.bucket ?? null, page_size])
  let offset = 0
  if (request.cursor) {
    let snapshot: unknown
    try { snapshot = JSON.parse(atob(request.cursor)) } catch { throw new MockError('invalid_request', 400) }
    if (!Array.isArray(snapshot) || snapshot.length !== 2 || snapshot[0] !== binding
      || !Number.isInteger(snapshot[1]) || snapshot[1] <= 0 || snapshot[1] >= rows.length) throw new MockError('invalid_request', 400)
    offset = snapshot[1] as number
  }
  const next = offset + page_size
  const { method, method_version, grain, selection, input_version, source_timezone, requested_window, calendar_versions, label_position, timezone_basis } = report
  return {
    method, method_version, grain, selection, input_version, source_timezone, requested_window, calendar_versions, label_position, timezone_basis,
    attribution: report.attribution,
    state: rows.length ? 'ok' : 'no_data', results: rows.slice(offset, next),
    page: { page_size, next_cursor: next < rows.length ? btoa(JSON.stringify([binding, next])) : null },
  }
}

export function scatterPoints(report: ResearchReport) {
  const { window, keys, pair } = fromIntent(report)
  if (report.method !== 'pearson') return []
  return (pair?.slots ?? []).flatMap((slot) => {
    const x = window.columns[keys[0]].values[slot]
    const y = window.columns[keys[1]].values[slot]
    return typeof x === 'number' && typeof y === 'number'
      ? [{ at: new Date(Date.parse(window.start) + hour * slot).toISOString(), x, y }]
      : []
  })
}

interface Saved extends Intent { id: string; title: string; intent_revision: number; created_at: string; updated_at: string }
export interface ExportTicket { id: string; kind: 'detail'; created_at: string; expires_at: string }
const privateSaved = new Map<string, { owner: string; conditions: Conditions; item: Saved }>()
const privateTickets = new Map<string, { owner: string; report: ResearchReport; ticket: ExportTicket }>()
const newId = () => crypto.randomUUID()

export function saveAnalysis(owner: string, title: string, conditions: Conditions): Saved {
  const name = title.trim()
  if (!name || name.length > 100) throw new MockError('invalid_request', 400)
  const now = new Date().toISOString()
  const item = { ...intentFor(conditions), id: newId(), title: name, intent_revision: 1, created_at: now, updated_at: now }
  privateSaved.set(item.id, { owner, conditions: { ...conditions, objectIds: [...conditions.objectIds] }, item })
  return item
}
export function listPlans(owner: string): Saved[] {
  return Array.from(privateSaved.values()).filter((entry) => entry.owner === owner).map((entry) => entry.item).reverse()
}
export function runSaved(owner: string, id: string): Conditions {
  const entry = privateSaved.get(id)
  if (entry?.owner !== owner) throw new MockError('not_found', 404)
  return { ...entry.conditions, objectIds: [...entry.conditions.objectIds] }
}

function verifyExport(report: ResearchReport, scenario: Scenario) {
  if (scenario === 'denied') throw new MockError('use_denied', 403)
  if (scenario === 'error') throw new MockError('database_unavailable', 503)
  if (report.input_version !== fromIntent(report, scenario === 'changed').version) throw new MockError('input_changed', 409)
}

export function requestExport(owner: string, report: ResearchReport, scenario: Scenario, now = Date.now()): ExportTicket | null {
  verifyExport(report, scenario)
  if (report.state === 'no_data') return null
  const ticket = { id: newId(), kind: 'detail' as const, created_at: new Date(now).toISOString(), expires_at: new Date(now + 15 * 60000).toISOString() }
  privateTickets.set(ticket.id, { owner, report, ticket })
  return ticket
}

export function downloadExport(owner: string, id: string, scenario: Scenario, now = Date.now()): string {
  const entry = privateTickets.get(id)
  if (entry?.owner !== owner) throw new MockError('not_found', 404)
  verifyExport(entry.report, scenario)
  if (now >= Date.parse(entry.ticket.expires_at)) throw new MockError('expired', 410)
  const report = entry.report
  const fields = ['group', 'source', 'product', 'attribution', 'generated_at', 'source_timezone', 'indicator', 'unit', 'window_definition', 'standard_series', 'input_version', 'method', 'method_version', 'entity', 'observed_at', 'value', 'quality', 'censor_direction', 'censor_limit', 'key', 'revision', 'fetched_at']
  const encode = (cell: string | number | null): string => cell === null ? '' : `"${String(cell).replaceAll('"', '""')}"`
  const rows = rowsFor(report, { support: 'observed' }).map((row) => [
    1, row.source, row.product, row.attribution, entry.ticket.created_at, row.source_timezone, row.indicator, row.unit,
    row.window_definition, row.standard_series, report.input_version, report.method, report.method_version,
    row.entity, row.observed_at, row.value, row.quality, row.censor_direction, row.censor_limit, row.key, row.revision, row.fetched_at,
  ].map(encode).join(','))
  return [fields.join(','), ...rows].join('\r\n') + '\r\n'
}
