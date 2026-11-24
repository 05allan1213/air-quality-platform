import { describe, expect, it } from 'vitest'
import { lineChart, styleFor, timeSeries } from './chartOptions'
import {
  defaultConditions, detailsFor, downloadExport, intentFor, listPlans, reportFor,
  requestExport, runSaved, saveAnalysis, scatterPoints,
} from './researchMock'

const analyst = 'preview-analyst-test'
const visitor = 'preview-visitor-test'

function nextPage(cursor: string | null) {
  expect(cursor).not.toBeNull()
  return cursor ?? undefined
}

describe('F 合成研究契约', () => {
  it('保留两站 18/24 的各自观测，按共同有效 12/24 限制比较结论', () => {
    const report = reportFor({ ...defaultConditions(), window: 'insufficient' })
    expect(report.columns.map(({ E, V }) => [E, V])).toEqual([[24, 18], [24, 18]])
    expect(report).toMatchObject({ E_common: 24, P: 12, paired_ratio: 0.5, ranking: null, mean_difference: null, conclusion: { state: 'insufficient_coverage' } })
    const lines = timeSeries(report)
    expect(lines[0].data.slice(18).every(({ value }) => value === null)).toBe(true)
    expect(lines[1].data.slice(0, 6).every(({ value }) => value === null)).toBe(true)
    expect(lineChart(report, lines, { start: 0, end: 100 }).series).toMatchObject([
      { connectNulls: false, smooth: false }, { connectNulls: false, smooth: false },
    ])
    expect(detailsFor(report, { support: 'paired', input_version: report.input_version }).results).toHaveLength(24)
  })

  it('区分缺小时、检出限记录、真实合成零值与可用浓度计数', () => {
    const report = reportFor({ ...defaultConditions('statistics'), indicator: 'SO2', grain: 'day' })
    expect(report.columns[0]).toMatchObject({ E: 24, O: 23, V: 22, exclusions: { gap: 1, censored: 1 } })
    const observed = detailsFor(report, { support: 'observed', input_version: report.input_version })
    const effective = detailsFor(report, { support: 'effective', input_version: report.input_version })
    expect(observed.results).toContainEqual(expect.objectContaining({ quality: 'censored', value: null, censor_direction: '<', censor_limit: '3.000000' }))
    expect(effective.results).toContainEqual(expect.objectContaining({ quality: 'valid', value: '0.000000' }))
    expect(effective.results.some(({ quality }) => quality === 'censored')).toBe(false)
    expect(report.distribution?.reduce((sum, bin) => sum + bin.n, 0)).toBe(effective.results.length)
    expect(timeSeries(report)[0].data[0].value).toBe(report.buckets?.[0].mean)
  })

  it('配对散点和分页明细沿用相同对象、窗口、指标顺序与输入版本', () => {
    const report = reportFor(defaultConditions('pearson'))
    expect(report).toMatchObject({ E_common: 48, P: 45, pearson: 0.962259, conclusion: { state: 'sufficient' } })
    const points = scatterPoints(report)
    expect(points).toHaveLength(45)
    const request = { support: 'paired', input_version: report.input_version, page_size: 12 } as const
    const first = detailsFor(report, request)
    const cursor = nextPage(first.page.next_cursor)
    const second = detailsFor(report, { ...request, cursor })
    expect(second).toMatchObject({ selection: first.selection, requested_window: first.requested_window, input_version: first.input_version, method_version: 's05.v1' })
    expect(second.results[0].observed_at).toBe(points[6].at)
    expect(second.results.slice(0, 2).map(({ value }) => Number(value))).toEqual([points[6].x, points[6].y])
    expect(first.results.some(({ key }) => second.results.some((row) => row.key === key))).toBe(false)
    expect(() => detailsFor(report, { ...request, support: 'observed', cursor })).toThrow('invalid_request')
    expect(reportFor({ ...defaultConditions('pearson'), window: 'normal' })).toMatchObject({ pearson: null, conclusion: { state: 'insufficient_pairs' } })
  })

  it('约束对象产品、数量和稳定的图例映射', () => {
    const conditions = defaultConditions()
    expect(() => intentFor({ ...conditions, objectIds: [201] })).toThrow('invalid_request')
    expect(() => intentFor({ ...conditions, objectIds: [101, 201] })).toThrow('incompatible_series')
    expect(() => intentFor({ ...conditions, objectIds: [1, 2, 3, 4, 5, 6] })).toThrow('query_too_large')
    const before = reportFor(conditions)
    const after = reportFor({ ...conditions, objectIds: [203, 202] })
    expect(styleFor(before.columns[1].entity)).toEqual(styleFor(after.columns[0].entity))
    expect(intentFor(defaultConditions('compare', true)).selection).toMatchObject({ scope: 'synthetic', product: 301, cities: [101, 102] })
  })

  it('在输入变化后拒绝旧明细和票据，重新应用恢复读取', () => {
    const conditions = defaultConditions()
    const original = reportFor(conditions)
    const ticket = requestExport(analyst, original, 'normal')
    expect(ticket).not.toBeNull()
    if (!ticket) return
    expect(() => detailsFor(original, { support: 'observed', input_version: original.input_version }, 'changed')).toThrow('input_changed')
    expect(() => downloadExport(analyst, ticket.id, 'changed')).toThrow('input_changed')
    const updated = reportFor(conditions, 'changed')
    expect(updated.input_version).not.toEqual(original.input_version)
    expect(detailsFor(updated, { support: 'observed', input_version: updated.input_version }, 'changed')).toMatchObject({ state: 'ok', input_version: updated.input_version })
  })

  it('按演示账号约束保存与下载，并在下载时复核许可和时限', () => {
    const conditions = defaultConditions()
    const saved = saveAnalysis(analyst, '站点对比方案', conditions)
    expect(listPlans(visitor)).toEqual([])
    expect(() => runSaved(visitor, saved.id)).toThrow('not_found')
    expect(runSaved(analyst, saved.id)).toEqual(conditions)
    const report = reportFor(conditions)
    const readAllowed = detailsFor(report, { support: 'observed', input_version: report.input_version }, 'denied')
    expect(readAllowed.state).toBe('ok')
    expect(() => requestExport(analyst, report, 'denied')).toThrow('use_denied')
    const now = Date.parse('2024-05-20T00:00:00Z')
    const ticket = requestExport(analyst, report, 'normal', now)
    expect(ticket).not.toBeNull()
    if (!ticket) return
    expect(() => downloadExport(visitor, ticket.id, 'normal', now)).toThrow('not_found')
    expect(() => downloadExport(analyst, ticket.id, 'denied', now)).toThrow('use_denied')
    expect(() => downloadExport(analyst, ticket.id, 'normal', now + 15 * 60_000)).toThrow('expired')
    const csv = downloadExport(analyst, ticket.id, 'normal', now)
    expect(csv).toContain('澄空观测 · 原型合成样本 F')
    expect(csv).toContain(report.input_version)
    expect(requestExport(analyst, reportFor({ ...conditions, window: 'empty' }), 'normal')).toBeNull()
  })
})
