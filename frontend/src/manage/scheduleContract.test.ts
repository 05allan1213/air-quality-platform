import { describe, expect, it } from 'vitest'
import {
  createScheduleDraftCommand,
  decideScheduleCommand,
  eligibleFirstDueAt,
  normalizedFirstDueAt,
  proposeScheduleCommand,
  type SchedulePlan,
  type ScheduleTemplateOption,
} from './scheduleContract'

const template: ScheduleTemplateOption = {
  template: 7,
  template_version: 3,
  product: 2,
  source: 'fixture.local',
  contract_version: 'fixture.v1',
  scope_label: 'North Sample',
  allowed_cadences: [3600, 21600],
  delivery_minute: 15,
  plan_state: 'unconfigured',
  job: null,
}

const draft: SchedulePlan = {
  job: 19,
  state: 'draft',
  version: 4,
  template: 7,
  template_version: 3,
  current_template_version: 3,
  cadence_seconds: 3600,
  first_due_at: '2027-01-01T00:15:00Z',
  predecessor: 18,
  predecessor_version: 8,
  pending_successor: null,
  effective_from: null,
  effective_until: null,
  preview: {
    boundary: '2027-01-01T00:00:00Z',
    previous_last_due_at: '2026-12-31T23:15:00Z',
    first_due_at: '2027-01-01T00:15:00Z',
    gap_seconds: 3600,
    conflict: null,
  },
}

function futureDue(minute: number) {
  const due = new Date(Date.now() + 4 * 60 * 60 * 1000)
  due.setUTCMinutes(minute, 0, 0)
  if (due.getTime() <= Date.now() + 3 * 60 * 60 * 1000) due.setUTCHours(due.getUTCHours() + 1)
  return due.toISOString().replace('.000Z', 'Z')
}

describe('采集计划契约', () => {
  it('仅接受有显式时区、秒为零且匹配模板投递分钟的首个时刻', () => {
    expect(normalizedFirstDueAt('2027-01-01T08:15:00+08:00', 15)).toBe('2027-01-01T08:15:00+08:00')
    expect(normalizedFirstDueAt('2027-01-01T08:45:00+08:30', 15)).toBe('2027-01-01T08:45:00+08:30')
    expect(normalizedFirstDueAt('2027-01-01T08:15:00+08:30', 15)).toBeNull()
    expect(normalizedFirstDueAt('2027-01-01T00:15:00', 15)).toBeNull()
    expect(normalizedFirstDueAt('2027-01-01T00:15:30Z', 15)).toBeNull()
    expect(normalizedFirstDueAt('2027-01-01T00:00:00Z', 15)).toBeNull()
    expect(normalizedFirstDueAt('2027-02-30T00:15:00Z', 15)).toBeNull()
  })

  it('创建停用草稿时绑定模板版本、允许频率与新请求 UUID', () => {
    const due = futureDue(template.delivery_minute)
    const command = createScheduleDraftCommand(template, 3600, due, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    expect(command).toEqual({
      action: 'create_draft', expected_version: 3, request_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      reason_code: 'schedule_create', template: 7, cadence_seconds: 3600, first_due_at: due,
    })
    expect(createScheduleDraftCommand(template, 7200, due, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).toBeNull()
    expect(createScheduleDraftCommand(template, 3600, '2026-01-01T00:15:00Z', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')).toBeNull()
    const sameHour = new Date()
    sameHour.setUTCMinutes(template.delivery_minute, 0, 0)
    expect(eligibleFirstDueAt(sameHour.toISOString().replace('.000Z', 'Z'), template.delivery_minute)).toBe(false)
  })

  it('后继草稿绑定现行活动计划版本，激活/丢弃绑定前驱版本', () => {
    const active = { ...draft, state: 'active' as const, version: 9, pending_successor: null }
    const due = futureDue(template.delivery_minute)
    expect(proposeScheduleCommand(active, template, 21600, due, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')).toMatchObject({
      action: 'propose', expected_version: 9, template_version: 3, reason_code: 'schedule_change',
    })
    expect(proposeScheduleCommand({ ...active, pending_successor: 20 }, template, 21600, due, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')).toBeNull()
    expect(decideScheduleCommand('activate', draft, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc')).toMatchObject({
      action: 'activate', expected_version: 4, expected_predecessor_version: 8, reason_code: 'schedule_activate',
    })
    expect(decideScheduleCommand('discard', { ...draft, predecessor: null, predecessor_version: null }, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc')).toMatchObject({
      action: 'discard', expected_predecessor_version: null, reason_code: 'schedule_discard',
    })
    expect(decideScheduleCommand('activate', active, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc')).toBeNull()
  })
})
