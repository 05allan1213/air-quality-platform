import { describe, expect, it } from 'vitest'
import { makeCity, makeHistory, makeTrend, displayValue, PRODUCTS, sourceTime, loadCity, directory } from './fixture'
import { defaultConditions, detailsFor, reportFor } from '../analysis/researchMock'

describe('F 城市与研究投影', () => {
  it('C 七项共享一个观测时刻，T 同小时保留自己的值和修订', () => {
    const city = makeCity('normal')
    const cRows = city.slots.flatMap(slot => slot.results)
    const tRows = makeTrend('normal').points.flatMap(p => p.results)
    expect(new Set(cRows.map(r => r.observed_at))).toEqual(new Set([city.snapshot_at]))
    expect(cRows).toHaveLength(7)
    expect(new Set(cRows.map(r => r.product))).toEqual(new Set([PRODUCTS.C]))
    expect(tRows).toHaveLength(24)
    expect(tRows.at(-1)).toMatchObject({ observed_at: city.snapshot_at, product: PRODUCTS.T, value: '41.000000' })
    expect(cRows[0]).toMatchObject({ value: '42.000000', color: '#00E400', grade: 1, standard_series:'HJ633-2012' })
    expect(cRows.find(r => r.indicator === 'CO')?.unit).toBe('mg/m³')
    expect(cRows.find(r => r.indicator === 'O3')).toMatchObject({unit:'µg/m³',report_kind:'hourly'})
    expect(cRows[0]?.observed_at).not.toBe(cRows[0]?.fetched_at)
  })
  it('部分状态显示检出限、缺测和 T 空槽，数值零可见', () => {
    const rows = makeCity('partial').slots.map(s => s.results[0]!)
    const missing = rows.find(r => r.indicator === 'NO2')!
    expect(displayValue(missing)).toBe('—')
    expect(displayValue(rows.find(r => r.indicator === 'SO2'))).toBe('<3')
    expect(displayValue({...missing, value: '0.000000', quality: 'valid'})).toBe('0')
    const gaps = makeTrend('partial').points.filter(p => p.state === 'no_data')
    expect(gaps).toHaveLength(4); expect(gaps.every(p => !p.results.length)).toBe(true)
  })
  it('指定空窗不回填，来源未知不推断在线或发布时间', async () => {
    expect(makeHistory('normal','2024-05-17','AQI').results).toHaveLength(0)
    expect(makeCity('empty').slots.every(slot => !slot.results.length)).toBe(true)
    expect(makeTrend('empty').points).toHaveLength(24)
    expect(makeCity('stale').slots[0]?.results[0]).toMatchObject({ freshness:'unknown',source_status:'unknown' })
    await expect(loadCity('error')).rejects.toMatchObject({ code:'ERR_FIXTURE' })
    expect(directory.every(s => s.coordinates === null && s.link.includes('scope=synthetic'))).toBe(true)
  })
  it('城市 PM2.5 历史与城市对比支撑记录在时间、观测值、键和修订上一致', () => {
    const report = reportFor(defaultConditions('statistics', true))
    const details = detailsFor(report, { support: 'observed', input_version: report.input_version, page_size: 50 })
    const history = makeHistory('normal','2024-05-16','PM2.5')
    expect(details.results).toHaveLength(history.results.length)
    for(const row of history.results) {
      const same = details.results.find(d => Date.parse(d.observed_at) === Date.parse(row.observed_at))
      expect(same).toMatchObject({key:row.key,revision:row.revision,unit:row.unit,product:row.product,source:row.source})
      expect(Number(same?.value)).toBe(Number(row.value))
    }
    expect(sourceTime(history.results[23]!.observed_at)).toBe('05-16 23:00')
  })
})
