import { describe, expect, it } from 'vitest'
import { canAdd, chartSupport, detailRequest, makeParent, observationValue, parentOf, seriesDiscoveryWindow, timeWindow, wholeQueryForResult, type Result, type Series } from './analysisContract'

const bounds = { start: '2024-01-01T00:00:00.000Z', end: '2024-01-31T00:00:00.000Z' }
const city = (overrides: Partial<Series> = {}): Series => ({
  product: 21, source: 'test.f', shape: 'C', city: 12, indicator: 'CO', unit: 'mg/m³',
  report_kind: 'hourly', window_definition: 'hourly_label_unspecified', standard_series: 'not_applicable',
  source_timezone: 'UTC', permitted_from: bounds.start, permitted_until: bounds.end, ...overrides,
})

describe('research query contract', () => {
  it('shares only the expressible selection from an applied single-series result', () => {
    const parent = makeParent('statistics', 'day', 'synthetic', bounds, [city()])!
    const result: Result = { ...parent, state: 'ok', input_version: 'analysis-version', requested_window: bounds,
      source_timezone: 'UTC', columns: [], conclusion: { state: 'sufficient' }, attribution: [],
      chart: { resolution: 'exact', points: [] } }
    expect(wholeQueryForResult(result, 'synthetic')).toEqual({ ...bounds, intent: 'whole', scope: 'synthetic',
      product: '21', source: 'test.f', city: '12', indicator: 'CO', unit: 'mg/m³', standard_series: 'not_applicable',
      report_kind: 'hourly', window_definition: 'hourly_label_unspecified',
    })
    expect(wholeQueryForResult(result, '')).toBeNull()
    expect(wholeQueryForResult({ ...result, method: 'compare' }, 'synthetic')).toBeNull()
    expect(wholeQueryForResult({ ...result, method: 'pearson' }, 'synthetic')).toBeNull()
    expect(wholeQueryForResult({ ...result, chart: undefined }, 'synthetic')).toBeNull()
    expect(wholeQueryForResult({ ...result, selection: { ...result.selection, end: '2024-02-01T00:00:00Z' } }, 'synthetic')).toBeNull()
  })

  it('checks exact UTC inputs, the half-open order and separate discovery and analysis limits', () => {
    expect(timeWindow('2024-02-30T00:00', '2024-03-02T00:00', 'statistics', 'day')).toBeNull()
    expect(timeWindow('2024-01-01T00:00', '2024-01-01T00:00', 'compare', 'hour')).toBeNull()
    expect(timeWindow('2024-01-01T00:00', '2024-01-31T00:01', 'compare', 'month')).toBeNull()
    expect(timeWindow('2024-01-01T00:00', '2025-01-01T00:00', 'statistics', 'season')).toEqual({
      start: bounds.start, end: '2025-01-01T00:00:00.000Z',
    })
    expect(timeWindow('2024-01-01T00:00', '2025-01-01T00:01', 'statistics', 'month')).toBeNull()
  })

  it('finds a series inside a later permitted slice without shortening the parent analysis', () => {
    const annual = timeWindow('2023-11-30T16:00', '2024-11-30T16:00', 'statistics', 'season')!
    expect(seriesDiscoveryWindow(annual)).toEqual({ start: annual.start, end: '2023-12-30T16:00:00.000Z' })
    expect(seriesDiscoveryWindow(annual, '2023-12-31T16:00')).toEqual({
      start: '2023-12-31T16:00:00.000Z', end: '2024-01-30T16:00:00.000Z',
    })
    expect(seriesDiscoveryWindow(annual, '2024-11-29T16:00')?.end).toBe(annual.end)
    expect(seriesDiscoveryWindow(annual, '2023-11-30T15:59')).toBeNull()
    expect(seriesDiscoveryWindow(annual, '2024-11-30T16:00')).toBeNull()
    expect(seriesDiscoveryWindow(annual, '2024-02-30T00:00')).toBeNull()
    expect(makeParent('statistics', 'season', 'synthetic', annual, [city()])?.selection).toMatchObject(annual)
  })

  it('preserves comparison order and accepts exactly the 5 × 720-hour bound', () => {
    const chosen = [17, 12, 19, 4, 3].map(id => city({ city: id }))
    expect(makeParent('compare', 'hour', 'synthetic', bounds, chosen)?.selection).toMatchObject({
      cities: [17, 12, 19, 4, 3], indicators: ['CO'], units: { CO: 'mg/m³' }, scope: 'synthetic',
    })
    expect(makeParent('compare', 'hour', 'synthetic', bounds, [...chosen, city({ city: 2 })])).toBeNull()
    expect(makeParent('compare', 'hour', 'synthetic', { ...bounds, end: '2024-01-31T00:01:00Z' }, chosen)).toBeNull()
    expect(makeParent('statistics', 'hour', '', bounds, [city(), city()])).toBeNull()
  })

  it('keeps product, layer, indicator, unit and window constraints and permits an unknown AQI standard for statistics only', () => {
    const selected = [city()]
    for (const mismatch of [{ product: 22 }, { shape: 'S' as const }, { indicator: 'PM10' }, { unit: 'µg/m³' }, { window_definition: 'hourly_right_label' }]) {
      expect(canAdd('compare', selected, city({ city: 13, ...mismatch }))).toBe(false)
    }
    const unknown = city({ indicator: 'AQI', standard_series: 'unknown', unit: '1' })
    expect(canAdd('statistics', [], unknown)).toBe(true)
    expect(canAdd('compare', [], unknown)).toBe(false)
    expect(canAdd('pearson', [], unknown)).toBe(false)
  })

  it('restricts Pearson to two concentrations on one object and safely uses a returned parent and support bucket', () => {
    const second = city({ indicator: 'PM10', unit: 'µg/m³' })
    expect(makeParent('pearson', 'day', '', bounds, [city(), { ...second, city: 13 }])).toBeNull()
    const parent = makeParent('pearson', 'day', '', bounds, [second, city()])!
    expect(parent.selection).toMatchObject({ cities: [12], indicators: ['PM10', 'CO'], scope: 'formal' })
    const applied = { ...parent, input_version: 'returned-version', attribution: ['F'] }
    const support = { support: 'effective' as const, bucket: { ...bounds, pos: 0, local_hour: 6 } }
    expect(detailRequest(applied, support, 'next-page')).toEqual({
      ...parent, input_version: 'returned-version', ...support, page_size: 20, cursor: 'next-page',
    })
    expect(parentOf(applied)).toEqual(parent)
  })

  it('does not turn null or censored values into numeric zero', () => {
    expect(observationValue({ value: '0', censor_limit: null, censor_direction: '', quality: 'valid' })).toBe('0')
    expect(observationValue({ value: null, censor_limit: '3', censor_direction: '<', quality: 'censored' })).toBe('<3')
    expect(observationValue({ value: null, censor_limit: null, censor_direction: '', quality: 'missing' })).toBe('—')
  })

  it('clips chart drill support to the applied parent window and keeps the series position', () => {
    const result = { requested_window: { start: bounds.start, end: bounds.end }, columns: [{ pos: 0 }, { pos: 1 }] }
    expect(chartSupport(result, bounds.start, 1)).toEqual({ support: 'observed', bucket: {
      start: bounds.start, end: '2024-01-01T01:00:00.000Z', pos: 1,
    } })
    expect(chartSupport(result, '2024-01-30T23:30:00.000Z', 0, 7200)).toEqual({ support: 'observed', bucket: {
      start: '2024-01-30T23:30:00.000Z', end: bounds.end, pos: 0,
    } })
    expect(chartSupport(result, bounds.end, 0)).toBeNull()
    expect(chartSupport(result, bounds.start, -1)).toBeNull()
  })
})
