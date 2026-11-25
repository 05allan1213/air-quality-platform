import { shallowRef } from 'vue'
import { ApiError } from '../api/client'
import type { ScheduleCommand, SchedulePlanState } from './scheduleContract'

type Envelope = { family: 'schedule'; target: number; command: ScheduleCommand }
export type RecordedSchedule = { action_id: string; job: number; state: SchedulePlanState; version: number }

// An interrupted write may outlive the route, never the current private session.
export const pendingSchedule = shallowRef<Envelope | null>(null)
window.addEventListener('air-quality:clear-reads', () => { pendingSchedule.value = null })

export function beginSchedule(command: ScheduleCommand, job: number | null): Envelope | undefined {
  const target = command.action === 'create_draft' ? command.template : job
  if (pendingSchedule.value || target === null || !Number.isSafeInteger(target) || target <= 0) return
  const envelope: Envelope = { family: 'schedule', target, command }
  pendingSchedule.value = envelope
  return envelope
}

export function scheduleReceipt(value: unknown, original: Envelope, lookup: boolean): RecordedSchedule {
  const row = value as Record<string, unknown> | null
  const action = original.command.action
  const state = action === 'activate' ? 'active' : action === 'discard' ? 'discarded' : 'draft'
  if (!row || typeof row.action_id !== 'string' || !/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(row.action_id) ||
      typeof row.version !== 'number' || !Number.isSafeInteger(row.version) || row.version < 0 || row.state !== state ||
      typeof row.job !== 'number' || !Number.isSafeInteger(row.job) || row.job <= 0 ||
      ((action === 'discard' || action === 'activate') && row.job !== original.target) ||
      (action === 'propose' && row.job === original.target) || (lookup && row.action !== action)) {
    throw new ApiError('服务未返回与本次操作一致的回执。', 502, 'invalid_response')
  }
  return { action_id: row.action_id, job: row.job, version: row.version, state }
}
