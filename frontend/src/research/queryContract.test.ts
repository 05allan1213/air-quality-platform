// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { canonicalDetail, catalogAnchor, matchesWholeQueryObject, normalizedInstant, parseWholeQueryIntent, rowValue, type CatalogObject, type DetailPage, type DetailParent, type ObservationRow } from './queryContract'

const parent: DetailParent = {
  method: 'detail', method_version: 's05.v1', grain: 'hour', selection: {
    product: 2, source: 'fixture.local', scope: 'synthetic', cities: [4, 7], indicators: ['NO2'],
    units: { NO2: 'µg/m³' }, standard_series: { NO2: 'not_applicable' }, quality: 'reliable', report_kind: 'hourly',
    window_definition: 'hourly_label_unspecified', start: '2024-02-01T09:00:00Z', end: '2024-02-01T12:00:00Z',
  },
}
const detail: DetailPage = {
  ...parent, source_timezone: 'UTC', input_version: '2'.repeat(64), state: 'ok', attribution: [],
  results: [], page: { page_size: 2, next_cursor: null },
}
const catalogItem: CatalogObject = {
  product: 2, shape: 'C', source: 'fixture.local', synthetic: true, city: 4, name: null,
  attribution: '', rights_attributions: [], observed_at: '2024-02-01T09:00:00Z', local_report_date: null, indicator: 'NO2',
  history_link: '/api/v1/observations/query/?product=2&scope=synthetic&cities=4&mode=window&report_kind=hourly&window_definition=hourly_label_unspecified&indicators=NO2&start=2024-02-01T09%3A00%3A00Z&end=2024-02-01T10%3A00%3A00Z',
}
const row: ObservationRow = {
  key: 1, revision: 1, city: 4, product: 2, source: 'fixture.local', indicator: 'NO2', observed_at: '2024-02-01T09:00:00Z',
  value: '0.000000', unit: 'µg/m³', standard_series: 'not_applicable', window_definition: 'hourly_label_unspecified',
  quality: 'valid', censor_direction: '', censor_limit: null,
}

describe('独立查询契约', () => {
  it('仅接收同域、可精确表示的单对象整组条件，并匹配原产品目录', () => {
    const handoff = {
      intent: 'whole', scope: 'synthetic', product: '2', source: 'fixture.local', city: '4',
      indicator: 'NO2', unit: 'µg/m³', standard_series: 'not_applicable', report_kind: 'hourly',
      window_definition: 'hourly_label_unspecified', start: parent.selection.start, end: parent.selection.end,
    }
    const intent = parseWholeQueryIntent(handoff, 'synthetic')!
    expect(intent).toMatchObject({ object: 4, entity: 'city', indicator: 'NO2', product: 2 })
    expect(matchesWholeQueryObject(intent, catalogItem)).toBe(true)
    expect(matchesWholeQueryObject(intent, { ...catalogItem, source: 'other' })).toBe(false)
    expect(matchesWholeQueryObject(intent, { ...catalogItem, city: undefined, site_epoch: 4 })).toBe(false)
    expect(matchesWholeQueryObject(intent, { ...catalogItem, shape: 'T' })).toBe(false)
    expect(parseWholeQueryIntent(handoff, '')).toBeNull()
    expect(parseWholeQueryIntent({ ...handoff, scope: undefined, city: undefined, site_epoch: '4' }, '')).toMatchObject({ entity: 'site_epoch' })
    for (const change of [{ product: ['2'] }, { city: '9007199254740993' }, { site_epoch: '4' },
      { unit: 'mg/m³' }, { standard_series: 'unknown' }, { report_kind: 'daily' }, { window_definition: 'no_window' },
      { end: '2024-03-03T09:00:00Z' }, { end: handoff.start }, { input_version: 'invalid' }, { support: 'paired' }]) {
      expect(parseWholeQueryIntent({ ...handoff, ...change }, 'synthetic')).toBeNull()
    }
  })

  it('接收同域、同对象的本地目录锚点，拒绝跨域与不合契约链接', () => {
    expect(catalogAnchor(catalogItem, 'synthetic')).toMatchObject({ start: '2024-02-01T09:00:00Z', window: 'hourly_label_unspecified' })
    expect(catalogAnchor(catalogItem, '')).toBeNull()
    expect(catalogAnchor({ ...catalogItem, city: 5 }, 'synthetic')).toBeNull()
    expect(catalogAnchor({ ...catalogItem, history_link: `https://example.org${catalogItem.history_link}` }, 'synthetic')).toBeNull()
  })

  it('整组父查询拒绝范围、单位、方法或版本意外变化', () => {
    const selection = { ...detail.selection, cities: [7, 4], start: '2024-02-01T17:00:00+08:00' }
    expect(canonicalDetail({ ...detail, selection }, parent)).toEqual({ ...parent, selection })
    expect(canonicalDetail({ ...detail, input_version: '17' }, parent)).toBeNull()
    expect(canonicalDetail({ ...detail, selection: { ...selection, cities: [4, 7, 10] } }, parent)).toBeNull()
    expect(canonicalDetail({ ...detail, selection: { ...selection, end: '2024-02-02T12:00:00Z' } }, parent)).toBeNull()
    expect(canonicalDetail({ ...detail, selection: { ...selection, units: { NO2: 'mg/m³' } } }, parent)).toBeNull()
    expect(canonicalDetail({ ...detail, grain: 'day' }, parent)).toBeNull()
  })

  it('时间必须有偏移并保持输入语义，零与删失与缺测各自显示', () => {
    expect(normalizedInstant('2024-02-29T09:00:00+08:00')).toBe('2024-02-29T09:00:00+08:00')
    expect(normalizedInstant('2024-02-29T09:00Z')).toBeNull()
    expect(normalizedInstant('2024-02-29T09:00')).toBeNull()
    expect(normalizedInstant('2024-02-30T09:00:00Z')).toBeNull()
    expect(rowValue(row)).toBe('0.000000')
    expect(rowValue({ ...row, quality: 'censored', value: null, censor_limit: '3.000000', censor_direction: '<' })).toBe('<3.000000')
    expect(rowValue({ ...row, quality: 'missing', value: null })).toBe('—')
  })
})
