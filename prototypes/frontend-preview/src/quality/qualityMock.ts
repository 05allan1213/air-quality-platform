export const operatorId = 701

export type Scenario = 'unordered' | 'verified' | 'resolved'
export type QualityStatus = 'open' | 'in_review' | 'resolved'
export type QualityAction = 'claim' | 'hold' | 'close' | 'correct_source'
export type QualityReason = 'claim_review' | 'retain_isolation' | 'review_complete' | 'verified_correction'
export type ErrorCode = 'invalid_request' | 'permission_denied' | 'precondition_conflict' | 'mapping_evidence_mismatch'

interface Affected {
  product: number
  synthetic: true
  geography: string
  object_code: string
  indicator: string
  start: string
  end: string
  site_epoch_id: number
  report_kind: 'hourly'
  window_definition: 'hourly_right_label'
  standard_series: 'not_applicable'
  observed_at: string
  query_types: readonly ['observation_detail', 'analysis', 'analysis_details', 'export']
}

interface QualityIssue {
  id: number
  event_version: number
  status: QualityStatus
  severity: 'blocking'
  code: 'unordered_conflict' | 'same_version_conflict'
  handler: number | null
  rule_version: number
  created_at: string
  resolved_at: string | null
  run: string | null
  run_item: number | null
  window: number | null
  window_start: string | null
  window_end: string | null
  batch: number
  receipt: number
  affected: Affected
  current_revision: number
  watermark: number
  qualification_version: number
  disputed: boolean
  candidate_revisions: number[]
  evidence: { correct_source?: string }
}

interface Revision {
  revision_id: number
  revision_no: number
  value: string
  unit: 'µg/m³'
  quality: 'valid'
  current: boolean
  candidate: boolean
  kind: 'initial' | 'source_revision' | 'unordered_conflict' | 'same_version_conflict'
  source_token: number | null
  rule: number
}

interface ActionReceipt {
  action_id: string
  status: QualityStatus
  event_version: number
  affected: readonly Affected[]
  outcome: QualityAction
}

interface LocalActionRecord {
  command: QualityCommand
  actor: number
  at: string
  response: ActionReceipt
}

// Observation evidence and local witness records are separate from the event detail projection.
export interface QualityExample {
  issue: QualityIssue
  observation: {
    synthetic: true
    trusted_scheme: 'sequence'
    revisions: Revision[]
  }
  receiptTime: string
  trustedReceipt: number
  localActions: LocalActionRecord[]
}

export interface QualityCommand {
  action: QualityAction
  reason_code: QualityReason | ''
  request_id: string
  expected_version: number
  evidence_ref: string | null
}

export const statusText: Record<QualityStatus, string> = {
  open: '待处理',
  in_review: '审阅中',
  resolved: '已处置',
}

export const actions = {
  claim: { label: '领取事件', reason: 'claim_review', reasonText: '开始审阅' },
  hold: { label: '保留隔离', reason: 'retain_isolation', reasonText: '证据不足，继续隔离' },
  close: { label: '关闭事件', reason: 'review_complete', reasonText: '审阅已完成' },
  correct_source: { label: '按来源证据解除隔离', reason: 'verified_correction', reasonText: '可信来源版本已核验' },
} as const satisfies Record<QualityAction, { label: string; reason: QualityReason; reasonText: string }>

export const errorText: Record<ErrorCode, string> = {
  invalid_request: '请选择与操作相符的处置理由。',
  permission_denied: '缺少质量裁决权限，操作未提交。',
  precondition_conflict: '事件版本或状态已变化。请重新打开处置预览。',
  mapping_evidence_mismatch: '当前证据无法支持该操作。请核对修订证据。',
}

const affected: Affected = {
  product: 302,
  synthetic: true,
  geography: 'F-云汀市',
  object_code: 'F-S01',
  indicator: 'PM2.5',
  start: '2024-05-16',
  end: '2024-05-17',
  site_epoch_id: 201,
  report_kind: 'hourly',
  window_definition: 'hourly_right_label',
  standard_series: 'not_applicable',
  observed_at: '2024-05-16T06:00:00Z',
  query_types: ['observation_detail', 'analysis', 'analysis_details', 'export'],
}

const submittedAt = '2024-05-16T06:25:00Z'
const finishedAt = '2024-05-16T07:05:00Z'
const evidenceRef = 'fixture.source.2419.3.701.6487'

function revision(id: number, no: number, value: string, token: number | null, kind: Revision['kind']): Revision {
  const candidate = kind === 'unordered_conflict' || kind === 'same_version_conflict'
  return {
    revision_id: id,
    revision_no: no,
    value,
    unit: 'µg/m³',
    quality: 'valid',
    current: !candidate,
    candidate,
    kind,
    source_token: token,
    rule: 1,
  }
}

export function createExample(scenario: Scenario): QualityExample {
  const isUnordered = scenario === 'unordered'
  const isResolved = scenario === 'resolved'
  const current = isUnordered
    ? revision(8021, 1, '42', 174, 'initial')
    : revision(8026, 3, '39', 176, 'source_revision')
  const candidate = isUnordered
    ? revision(8022, 2, '71', null, 'unordered_conflict')
    : revision(8025, 2, '64', 174, 'same_version_conflict')

  const issue: QualityIssue = {
    id: isUnordered ? 2418 : 2419,
    event_version: isUnordered ? 1 : isResolved ? 4 : 3,
    status: isResolved ? 'resolved' : 'open',
    severity: 'blocking',
    code: isUnordered ? 'unordered_conflict' : 'same_version_conflict',
    handler: isResolved ? operatorId : null,
    rule_version: 1,
    created_at: submittedAt,
    resolved_at: isResolved ? finishedAt : null,
    run: null,
    run_item: null,
    window: null,
    window_start: null,
    window_end: null,
    batch: isUnordered ? 4687 : 4688,
    receipt: isUnordered ? 6484 : 6485,
    affected,
    current_revision: current.revision_id,
    watermark: isUnordered ? 174 : 176,
    qualification_version: isResolved ? 3 : 2,
    disputed: !isResolved,
    candidate_revisions: isResolved ? [] : [candidate.revision_id],
    evidence: isUnordered || isResolved ? {} : { correct_source: evidenceRef },
  }

  const previous = {
    command: {
      action: 'correct_source',
      reason_code: 'verified_correction',
      request_id: '00000000-0000-4000-8000-000000000070',
      expected_version: 3,
      evidence_ref: evidenceRef,
    },
    actor: operatorId,
    at: finishedAt,
    response: {
      action_id: '00000000-0000-4000-8000-000000000071',
      status: 'resolved',
      event_version: 4,
      affected: [issue.affected],
      outcome: 'correct_source',
    },
  } satisfies LocalActionRecord

  return {
    issue,
    observation: { synthetic: true, trusted_scheme: 'sequence', revisions: [current, candidate] },
    receiptTime: '2024-05-16T06:24:00Z',
    trustedReceipt: isUnordered ? 6483 : 6487,
    localActions: isResolved ? [previous] : [],
  }
}

export function isActionAvailable(example: QualityExample, action: QualityAction): boolean {
  const issue = example.issue
  if (issue.status === 'resolved') return false
  if (action === 'claim') return issue.status === 'open'
  if (action === 'correct_source') return Boolean(issue.evidence.correct_source)
  return issue.status === 'open' || issue.handler === operatorId
}

export function performAction(
  example: QualityExample,
  command: QualityCommand,
  permission: boolean,
  at: string,
  receiptId: string,
): { response: ActionReceipt; example: QualityExample } | { code: ErrorCode } {
  if (!permission) return { code: 'permission_denied' }
  if (command.reason_code !== actions[command.action].reason) return { code: 'invalid_request' }
  const issue = example.issue
  if (issue.event_version !== command.expected_version || issue.status === 'resolved') {
    return { code: 'precondition_conflict' }
  }
  const correcting = command.action === 'correct_source'
  if (correcting) {
    if (!command.evidence_ref || command.evidence_ref !== issue.evidence.correct_source) {
      return { code: 'mapping_evidence_mismatch' }
    }
  } else if (command.evidence_ref !== null) {
    return { code: 'invalid_request' }
  }
  if (!isActionAvailable(example, command.action)) return { code: 'precondition_conflict' }

  const status = correcting || command.action === 'close' ? 'resolved' : 'in_review'
  const response: ActionReceipt = {
    action_id: receiptId,
    status,
    event_version: issue.event_version + 1,
    affected: [issue.affected],
    outcome: command.action,
  }
  const nextIssue: QualityIssue = {
    ...issue,
    status,
    handler: operatorId,
    resolved_at: status === 'resolved' ? at : null,
    event_version: response.event_version,
    disputed: correcting ? false : issue.disputed,
    qualification_version: issue.qualification_version + Number(correcting),
    candidate_revisions: correcting ? [] : issue.candidate_revisions,
    evidence: status === 'resolved' || !issue.evidence.correct_source
      ? {}
      : { correct_source: `fixture.source.${issue.id}.${response.event_version}.${operatorId}.${example.trustedReceipt}` },
  }
  return {
    response,
    example: {
      ...example,
      issue: nextIssue,
      localActions: [...example.localActions, { command, actor: operatorId, at, response }],
    },
  }
}
