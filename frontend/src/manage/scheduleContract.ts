export type SchedulePlanState = 'draft' | 'active' | 'discarded'

export type ScheduleTemplateOption = {
  template: number
  template_version: number
  product: number
  source: string
  contract_version: string
  scope_label: string
  allowed_cadences: number[]
  delivery_minute: number
  plan_state: 'unconfigured' | 'draft' | 'active'
  job: number | null
}

export type ScheduleTemplatePage = {
  results: ScheduleTemplateOption[]
  next_cursor: string | null
}

export type SchedulePreview = {
  boundary: string
  previous_last_due_at: string | null
  first_due_at: string
  gap_seconds: number | null
  conflict: 'registered_window' | null
}

export type SchedulePlan = {
  job: number
  state: SchedulePlanState
  version: number
  template: number
  template_version: number
  current_template_version: number
  cadence_seconds: number
  first_due_at: string
  predecessor: number | null
  predecessor_version: number | null
  pending_successor: number | null
  effective_from: string | null
  effective_until: string | null
  preview: SchedulePreview
}

export type ScheduleReceipt = SchedulePlan & { action_id: string }

export type ScheduleCreateCommand = {
  action: 'create_draft'
  expected_version: number
  request_id: string
  reason_code: 'schedule_create'
  template: number
  cadence_seconds: number
  first_due_at: string
}

export type ScheduleChangeCommand =
  | {
      action: 'propose'
      expected_version: number
      request_id: string
      reason_code: 'schedule_change'
      template_version: number
      cadence_seconds: number
      first_due_at: string
    }
  | {
      action: 'activate'
      expected_version: number
      request_id: string
      reason_code: 'schedule_activate'
      template_version: number
      expected_predecessor_version: number | null
    }
  | {
      action: 'discard'
      expected_version: number
      request_id: string
      reason_code: 'schedule_discard'
      template_version: number
      expected_predecessor_version: number | null
    }

export type ScheduleCommand = ScheduleCreateCommand | ScheduleChangeCommand

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DUE_AT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):00(?:Z|([+-])(\d{2}):(\d{2}))$/

function validId(value: number | null) {
  return value !== null && Number.isSafeInteger(value) && value > 0
}

function validVersion(value: number | null) {
  return value !== null && Number.isSafeInteger(value) && value >= 0
}

function validRequestId(value: string) {
  return UUID.test(value)
}

export function normalizedFirstDueAt(value: string, deliveryMinute: number): string | null {
  const text = value.trim()
  const match = DUE_AT.exec(text)
  if (!match || !Number.isFinite(Date.parse(text))) return null
  const [, year, month, day, hour, minute, , offsetHour, offsetMinute] = match
  if (Number(hour) > 23 || Number(minute) > 59 || Number(offsetHour ?? 0) > 23 || Number(offsetMinute ?? 0) > 59) return null
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day)) return null
  if (new Date(text).getUTCMinutes() !== deliveryMinute) return null
  return text
}

export function eligibleFirstDueAt(value: string, deliveryMinute: number, now = Date.now()) {
  const due = normalizedFirstDueAt(value, deliveryMinute)
  if (!due) return false
  const nextHour = Math.floor(now / 3_600_000) * 3_600_000 + 3_600_000
  return Math.floor(Date.parse(due) / 3_600_000) * 3_600_000 > now && Date.parse(due) >= nextHour
}

export function cadenceLabel(seconds: number) {
  const hours = seconds / 3600
  return hours === 1 ? '每小时' : `每 ${hours} 小时`
}

export function createScheduleDraftCommand(
  option: ScheduleTemplateOption,
  cadence: number,
  firstDueAt: string,
  requestId: string,
): ScheduleCreateCommand | null {
  const due = normalizedFirstDueAt(firstDueAt, option.delivery_minute)
  if (!validId(option.template) || !validVersion(option.template_version) || !option.allowed_cadences.includes(cadence) ||
      !due || !eligibleFirstDueAt(due, option.delivery_minute) || !validRequestId(requestId)) return null
  return {
    action: 'create_draft',
    expected_version: option.template_version,
    request_id: requestId,
    reason_code: 'schedule_create',
    template: option.template,
    cadence_seconds: cadence,
    first_due_at: due,
  }
}

export function proposeScheduleCommand(
  plan: SchedulePlan,
  option: ScheduleTemplateOption,
  cadence: number,
  firstDueAt: string,
  requestId: string,
): Extract<ScheduleChangeCommand, { action: 'propose' }> | null {
  const due = normalizedFirstDueAt(firstDueAt, option.delivery_minute)
  if (plan.state !== 'active' || plan.pending_successor !== null || !validId(plan.job) ||
      !validVersion(plan.version) || !validVersion(plan.current_template_version) ||
      !option.allowed_cadences.includes(cadence) || !due || !eligibleFirstDueAt(due, option.delivery_minute) || !validRequestId(requestId)) return null
  return {
    action: 'propose',
    expected_version: plan.version,
    request_id: requestId,
    reason_code: 'schedule_change',
    template_version: plan.current_template_version,
    cadence_seconds: cadence,
    first_due_at: due,
  }
}

export function decideScheduleCommand(
  action: 'activate' | 'discard',
  plan: SchedulePlan,
  requestId: string,
): Extract<ScheduleChangeCommand, { action: 'activate' }> | Extract<ScheduleChangeCommand, { action: 'discard' }> | null {
  if (plan.state !== 'draft' || !validId(plan.job) || !validVersion(plan.version) ||
      !validVersion(plan.current_template_version) ||
      (plan.predecessor_version !== null && !validVersion(plan.predecessor_version)) || !validRequestId(requestId)) return null
  const common = {
    expected_version: plan.version,
    request_id: requestId,
    template_version: plan.current_template_version,
    expected_predecessor_version: plan.predecessor_version,
  }
  return action === 'activate'
    ? { action, ...common, reason_code: 'schedule_activate' }
    : { action, ...common, reason_code: 'schedule_discard' }
}
