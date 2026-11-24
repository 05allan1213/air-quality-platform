import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import axios, { AxiosError, type AxiosAdapter } from 'axios'
import concentrations from '../analysis/city-series.json'
import type { CityState } from '../shared/preview'

dayjs.extend(utc)
export const PRIMARY_CITY = 101
export const PRODUCTS = { C: 301, T: 303, S: 302 } as const
export const SOURCE = 'preview.f'
export const CITY_NAME = '云汀市'
export const ANCHOR = '2024-05-16T23:00:00+08:00'
export const STANDARD = 'HJ633-2012'
export const indicators = ['AQI', 'PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3'] as const
export type Indicator = (typeof indicators)[number]
export const pollutantNames: Record<Indicator, string> = { AQI: '空气质量指数', 'PM2.5': '细颗粒物', PM10: '可吸入颗粒物', SO2: '二氧化硫', NO2: '二氧化氮', CO: '一氧化碳', O3: '臭氧' }
export const formula: Record<Indicator, string> = { AQI: 'AQI', 'PM2.5': 'PM₂.₅', PM10: 'PM₁₀', SO2: 'SO₂', NO2: 'NO₂', CO: 'CO', O3: 'O₃' }
export const grades = ['优', '良', '轻度污染', '中度污染', '重度污染', '严重污染']
export const colors = ['#00E400', '#FFFF00', '#FF7E00', '#FF0000', '#99004C', '#7E0023']
export function sourceTime(iso: string, format = 'MM-DD HH:mm') { return dayjs.utc(iso).utcOffset(8).format(format) }
const start = Date.parse('2024-05-16T00:00:00+08:00')
const hour = 3600_000
const stamp = (milliseconds: number) => new Date(milliseconds).toISOString()
// Preset F readings. T is a separate, independently authored product with its own keys.
const tReadings = [38, 37, 34, 32, 34, 39, 42, 48, 54, 56, 51, 47, 44, 45, 50, 54, 52, 47, 45, 41, 38, 36, 39, 41]
const cAqiHistory = [37, 36, 33, 31, 33, 38, 41, 47, 52, 55, 50, 46, 43, 44, 48, 53, 51, 46, 44, 40, 39, 37, 40, 42]
const tGaps = [5, 6, 16, 17]
export const directory = [
  { station: 401, site_epoch: 201, station_name: '江湾站', codes: ['F-S01'] },
  { station: 402, site_epoch: 202, station_name: '北岸站', codes: ['F-S02'] },
  { station: 403, site_epoch: 203, station_name: '湖西站', codes: ['F-S03'] },
].map(site => ({ ...site, city: PRIMARY_CITY, city_name: CITY_NAME, coordinates: null, status: 'active', period_start: '2024-01-01T00:00:00+08:00', period_end: null, link: `/api/v1/stations/${site.station}/?product=${PRODUCTS.S}&site_epoch=${site.site_epoch}&scope=synthetic` }))
function reading(indicator: Indicator, value: number, index: number, product: number, state: CityState) {
  const at = start + index * hour
  const missing = state === 'partial' && product === PRODUCTS.C && index === 23 && indicator === 'NO2'
  const censored = state === 'partial' && product === PRODUCTS.C && index === 23 && indicator === 'SO2'
  const aq = indicator === 'AQI'
  const old = state === 'stale'
  const grade = aq ? (value <= 50 ? 1 : 2) : null
  const id = product === PRODUCTS.C && !aq
    ? PRIMARY_CITY * 10000 + (indicators.indexOf(indicator) - 1) * 100 + 49 + index
    : product * 10000 + index
  const fetched = at + (product === PRODUCTS.T ? 5 : 9) * 60000
  const evaluated = start + (old ? 38 : 23) * hour + 12 * 60000
  return {
    product, source: SOURCE, synthetic: true as const, city: PRIMARY_CITY,
    indicator, observed_at: stamp(at), fetched_at: stamp(fetched), fetched_at_basis: 'contributing_batch', source_local_date: '2024-05-16',
    evaluated_at: stamp(evaluated), age_seconds: (evaluated - at) / 1000, freshness: 'unknown',
    source_status: evaluated >= fetched + hour ? 'unknown' : 'responded', execution_started_at: stamp(fetched - 30000),
    source_event: 'responded', source_event_at: stamp(fetched), source_status_until: stamp(fetched + hour),
    key: id, revision: id + 10_000_000, revision_no: 1, qualification_version: 1,
    window_definition: 'hourly_label_unspecified', report_kind: 'hourly', standard_series: aq ? STANDARD : 'not_applicable',
    value: missing || censored ? null : value.toFixed(6), unit: aq ? '1' : indicator === 'CO' ? 'mg/m³' : 'µg/m³',
    quality: missing ? 'missing' : censored ? 'censored' : 'valid', censor_limit: censored ? '3.000000' : null, censor_direction: censored ? '<' : '',
    grade, color: grade ? colors[grade - 1] : null, attribution: '澄空观测 · 原型合成样本 F',
  }
}
export type Row = ReturnType<typeof reading>
export function displayValue(row?: Row) {
  if (row?.quality === 'censored' && row.censor_limit !== null) return `${row.censor_direction}${Number(row.censor_limit)}`
  return row?.value == null ? '—' : String(Number(row.value))
}
function cValue(indicator: Indicator, index: number) {
  if (indicator === 'AQI') return cAqiHistory[index]!
  return concentrations[indicator][index]!
}
export function makeCity(state: CityState) {
  const at = stamp(start + 23 * hour)
  const evaluated = stamp(start + (state === 'stale' ? 38 : 23) * hour + 12 * 60000)
  const base = { city: PRIMARY_CITY, source: SOURCE, synthetic: true as const, evaluated_at: evaluated, standard_series: STANDARD }
  const slots = indicators.map(indicator => ({
    indicator, state: state === 'empty' ? 'unavailable' : 'ok',
    results: state === 'empty' ? [] : [reading(indicator, cValue(indicator, 23), 23, PRODUCTS.C, state)],
  }))
  return {
    ...base, product: PRODUCTS.C, city_name: CITY_NAME, snapshot_at: at, state: state === 'empty' ? 'no_data' : 'ok', slots,
    links: {
      trend: [{ product: PRODUCTS.T, href: `/api/v1/cities/${PRIMARY_CITY}/trend/?scope=synthetic&product=${PRODUCTS.T}&anchor=${encodeURIComponent(at)}` }],
      stations: [{ product: PRODUCTS.S, href: `/api/v1/cities/${PRIMARY_CITY}/stations/?scope=synthetic&product=${PRODUCTS.S}&at=${encodeURIComponent(at)}` }],
      history: [`/api/v1/observations/query/?product=${PRODUCTS.C}&scope=synthetic&cities=${PRIMARY_CITY}&mode=window&indicators=AQI&report_kind=hourly&window_definition=hourly_label_unspecified&standard_series=${STANDARD}&start=${stamp(start)}&end=${stamp(start + 24 * hour)}`],
    },
  }
}
export function makeTrend(state: CityState) {
  return {
    city: PRIMARY_CITY, source: SOURCE, synthetic: true as const, evaluated_at: makeCity(state).evaluated_at, product: PRODUCTS.T, standard_series: STANDARD,
    anchor: stamp(start + 23 * hour), anchor_basis: 'explicit', start: stamp(start), end: stamp(start + 24 * hour), state: state === 'empty' ? 'no_data' : 'ok',
    points: tReadings.map((value, index) => {
      const gap = state === 'empty' || (state === 'partial' && tGaps.includes(index))
      return { observed_at: stamp(start + index * hour), state: gap ? 'no_data' : 'ok', results: gap ? [] : [reading('AQI', value, index, PRODUCTS.T, state)] }
    }),
  }
}
export function makeHistory(state: CityState, date: string, indicator: Indicator) {
  const rows = date === '2024-05-16' && state !== 'empty' ? Array.from({ length: 24 }, (_, i) => reading(indicator, cValue(indicator, i), i, PRODUCTS.C, state)) : []
  const from = `${date}T00:00:00+08:00`
  const selection = { product: PRODUCTS.C, source: SOURCE, cities: [PRIMARY_CITY], scope: 'synthetic', indicators: [indicator], mode: 'window', report_kind: 'hourly', window_definition: 'hourly_label_unspecified', start: stamp(Date.parse(from)), end: stamp(Date.parse(from) + 24 * hour) }
  return { selection, evaluated_at: makeCity(state).evaluated_at, synthetic: true as const, state: rows.length ? 'ok' : 'no_data', results: rows, page: { page_size: 50, next_cursor: null } }
}
export type CityPanel = ReturnType<typeof makeCity>
export type TrendPanel = ReturnType<typeof makeTrend>
export type HistoryPanel = ReturnType<typeof makeHistory>
export function mockStation(epoch: number) {
  const site = directory.find(item => item.site_epoch === epoch)
  if (!site) return undefined
  return { ...site, product: PRODUCTS.S, source: SOURCE, synthetic: true, as_of: ANCHOR }
}
// All requests terminate in this adapter. There is no network or backend fallback.
const offline: AxiosAdapter = async config => {
  const state: CityState = config.params.fixture
  await new Promise(resolve => setTimeout(resolve, 170))
  if (state === 'error') throw new AxiosError('读取 F 样本失败', 'ERR_FIXTURE', config)
  let data: unknown
  if (config.params.scope !== 'synthetic') throw new AxiosError('use_denied', 'ERR_FIXTURE_SCOPE', config)
  switch (config.url) {
    case `/api/v1/cities/${PRIMARY_CITY}/`: data = makeCity(state); break
    case `/api/v1/cities/${PRIMARY_CITY}/trend/`: data = makeTrend(state); break
    case '/api/v1/observations/query/': {
      data = makeHistory(state, sourceTime(config.params.start, 'YYYY-MM-DD'), config.params.indicators)
      break
    }
    default: throw new AxiosError('未注册的原型读取', 'ERR_FIXTURE_PATH', config)
  }
  return { data, status: 200, statusText: 'OK', config, headers: {} }
}
const client = axios.create({ adapter: offline })
export async function loadCity(state: CityState) {
  const params = { scope: 'synthetic', product: PRODUCTS.C, fixture: state, at: ANCHOR, standard_series: STANDARD }
  const [city, trend] = await Promise.all([
    client.get<CityPanel>(`/api/v1/cities/${PRIMARY_CITY}/`, { params }),
    client.get<TrendPanel>(`/api/v1/cities/${PRIMARY_CITY}/trend/`, { params: { scope: 'synthetic', product: PRODUCTS.T, fixture: state, anchor: ANCHOR, standard_series: STANDARD } }),
  ])
  return { city: city.data, trend: trend.data }
}
export async function loadHistory(state: CityState, date: string, indicator: Indicator) {
  const from = `${date}T00:00:00+08:00`
  const params = { scope: 'synthetic', cities: PRIMARY_CITY, product: PRODUCTS.C, mode: 'window', fixture: state, indicators: indicator, report_kind: 'hourly', window_definition: 'hourly_label_unspecified', ...(indicator === 'AQI' && { standard_series: STANDARD }), start: from, end: stamp(Date.parse(from) + 24 * hour) }
  return (await client.get<HistoryPanel>('/api/v1/observations/query/', { params })).data
}
