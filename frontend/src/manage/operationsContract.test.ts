import { describe, expect, it } from 'vitest'
import {
  allowedAuditKinds, auditObjectId, auditWindow, clockText, filterError, filterParams,
  filtersFromRoute, operationsIndicators, periodError, positiveId, ratioText, secondsText, timingText,
} from './operationsContract'

describe('系统运行的筛选和诊断口径', () => {
  it('按源地方日核对半开区间，拒绝空区间、超限和非实际日期', () => {
    expect(periodError('2024-02-01', '2024-03-02')).toBe('')
    expect(periodError('2024-02-01', '2024-03-03')).not.toBe('')
    expect(periodError('2024-02-30', '2024-03-03')).not.toBe('')
    expect(periodError('2024-02-01', '2024-02-01')).not.toBe('')
    expect(periodError('2024-02-01', '')).not.toBe('')
  })

  it('深链不选择重复参数，不发送空或通配条件，拒绝不能精确表示的编号', () => {
    const filters = filtersFromRoute({ product: '13', start: '2024-02-01', end: '2024-02-02', indicator: ['AQI', 'CO'] })
    expect(filterError(filters)).toBe('')
    expect(filterParams(filters)).toEqual({ product: '13', start: '2024-02-01', end: '2024-02-02' })
    expect(filterError({ ...filters, object_code: '*' })).not.toBe('')
    expect(positiveId('9007199254740991')).toBe(true)
    expect(positiveId('9007199254740992')).toBe(false)
  })

  it('不以零替代未知的时刻、时长或分母', () => {
    expect(clockText(null)).toBe('—')
    expect(clockText('2024-02-01T08:00:00+08:00')).toBe('2024-02-01 00:00:00 UTC')
    expect(secondsText(null)).toBe('—')
    expect(secondsText(0)).toBe('0 秒')
    expect(ratioText({ numerator: 0, denominator: 0, ratio: null })).toBe('—（分母为 0）')
    expect(ratioText({ numerator: 0, denominator: null, ratio: null })).toBe('—（分母未知）')
    expect(ratioText({ numerator: 0, denominator: 5, ratio: 0 })).toBe('0%（0/5）')
    expect(timingText({ sample_count: 3, known_count: 0, mean_seconds: null })).toBe('—（已知 0/3）')
  })

  it('按产品区分污染物浓度和等级，T 只支持 AQI', () => {
    expect(operationsIndicators('C')).toContain('PM2.5')
    expect(operationsIndicators('C')).not.toContain('PM2.5Level')
    expect(operationsIndicators('L')).toContain('PM2.5Level')
    expect(operationsIndicators('T')).toEqual(['AQI'])
    expect(operationsIndicators('unrecognized')).toEqual([])
    expect(operationsIndicators('__proto__')).toEqual([])
  })
})

describe('对象审计只接受具名能力的单对象查询', () => {
  it('纯诊断和账号管理无对象审计，Job、站点与许可各自核权', () => {
    expect(allowedAuditKinds(['diagnose_operations', 'manage_accounts', 'manage_catalog'])).toEqual([])
    expect(allowedAuditKinds(['configure_collection_schedule'])).toEqual(['job'])
    expect(allowedAuditKinds(['review_observation', 'manage_catalog', 'manage_grants'])).toEqual(['run', 'quality_issue', 'station', 'grant'])
  })

  it('严格检查 int64、规范 UUID 和有界 UTC 区间', () => {
    expect(auditObjectId('station', '9223372036854775807')).toBe(true)
    expect(auditObjectId('station', '9223372036854775808')).toBe(false)
    expect(auditObjectId('station', '01')).toBe(false)
    expect(auditObjectId('run', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).toBe(true)
    expect(auditObjectId('run', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA')).toBe(false)
    expect(auditWindow('2024-02-01T00:00', '2024-03-02T00:00')).toEqual({
      start: '2024-02-01T00:00:00.000Z', end: '2024-03-02T00:00:00.000Z',
    })
    expect(auditWindow('2024-02-01T00:00', '2024-03-02T00:01')).toBeNull()
    expect(auditWindow('2024-02-30T00:00', '2024-03-02T00:00')).toBeNull()
  })
})
