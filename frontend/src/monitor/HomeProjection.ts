import { localApiHref, type PageInfo } from './reads'

type ProductLink = { product: number; href: string }

export type HomeReading = {
  revision: number
  indicator: string
  value: string | null
  unit: string
  quality: string
  grade: number | null
  comparable: boolean
  standard_series: string
  window_definition: string
  observed_at: string
  source_timezone: string | null
  attribution: string | null
  rights_attributions: string[]
  freshness: string
  primary_pollutant?: string | null
  source_description?: string | null
}
export type HomeCity = {
  city: number
  name: string | null
  geography: string | null
  series: HomeReading[]
  levels: HomeReading[]
  links: { city?: ProductLink[]; trend?: ProductLink[] }
}
export type NationalResponse = {
  selection: { source: string; product: number; window_definition: string }
  synthetic: boolean
  snapshot_at: string | null
  evaluated_at: string
  state: 'ok' | 'no_data'
  summary: {
    state: 'ok' | 'unavailable'
    covered_city_count: number | null
    summary_eligible_city_count: number | null
    expected: number | null
    completeness: 'complete' | 'partial' | 'unknown'
    groups?: Record<string, { ranked_city_count: number; distribution: Record<string, number> }>
  }
  results: HomeCity[]
  page: PageInfo
  ranking: { state: 'ok' | 'unavailable'; rows: (Pick<HomeCity, 'city' | 'name' | 'geography'> & { aqi: string; grade: number; rank: number })[] }
  focused: HomeCity | null
}

export const gradeLabels = ['优', '良', '轻度污染', '中度污染', '重度污染', '严重污染']
export const knownStandards = ['HJ633-2012', 'HJ633-2026']
export const gradeLabel = (grade: number | null) => gradeLabels[(grade ?? 0) - 1] ?? '等级未知'
export const standardLabel = (standard: string) => standard === 'unknown' ? '标准未知' : standard
export const cityLabel = (row: Pick<HomeCity, 'name' | 'city'>) => row.name ?? `城市 ${row.city}`
const utcFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' })
export const utcLabel = (value: string) => {
  const time = Date.parse(value)
  return Number.isFinite(time) ? `${utcFormat.format(time)} UTC` : '时刻未知'
}

export function homeCityLink(row: HomeCity, kind: 'city' | 'trend', scope?: string, anchor?: string | null) {
  for (const link of row.links[kind] ?? []) {
    const target = localApiHref(link.href)
    const path = `cities/${row.city}/${kind === 'trend' ? 'trend/' : ''}`
    const allowed = kind === 'trend' ? ['product', 'scope', 'anchor', 'standard_series'] : ['product', 'scope', 'at', 'as_of', 'standard_series']
    if (!target || target.path !== path || target.params.product !== String(link.product)
      || target.params.scope !== scope || Object.keys(target.params).some(key => !allowed.includes(key))) continue
    if (anchor && Date.parse(target.params[kind === 'trend' ? 'anchor' : 'at'] ?? '') !== Date.parse(anchor)) continue
    return target
  }
  return null
}

type HomeReturn = { path: string; generation: number; position: unknown; cursors: string[]; city: number; entrance: 'detail' | 'table'; scroll: number }
let returnPage: HomeReturn | undefined

export function rememberHomeReturn(value?: HomeReturn) { returnPage = value }

export function takeHomeReturn(path: string, generation: number, position: unknown) {
  const value = returnPage
  returnPage = undefined
  return value?.path === path && value.generation === generation && value.position === position ? value : undefined
}

if (typeof window !== 'undefined') {
  window.addEventListener('air-quality:clear-reads', () => rememberHomeReturn())
}
