import { describe, expect, it } from 'vitest'
import { actions, createExample, isActionAvailable, performAction, type QualityAction, type QualityCommand, type QualityExample } from './qualityMock'

const at = '2024-05-16T07:05:00Z'
const id = '00000000-0000-4000-8000-000000000001'

function command(example: QualityExample, action: QualityAction): QualityCommand {
  return {
    action,
    reason_code: actions[action].reason,
    request_id: id,
    expected_version: example.issue.event_version,
    evidence_ref: action === 'correct_source' ? example.issue.evidence.correct_source ?? null : null,
  }
}

describe('合成质量事件处置', () => {
  it('关闭无序异值工单保留争议和候选', () => {
    const example = createExample('unordered')
    expect(isActionAvailable(example, 'correct_source')).toBe(false)
    const result = performAction(example, command(example, 'close'), true, at, id)
    expect('response' in result && result.response.status).toBe('resolved')
    if ('code' in result) throw new Error(result.code)
    expect(result.example.issue).toMatchObject({
      disputed: true,
      current_revision: example.issue.current_revision,
      candidate_revisions: example.issue.candidate_revisions,
      watermark: example.issue.watermark,
      qualification_version: example.issue.qualification_version,
    })
    expect(isActionAvailable(result.example, 'hold')).toBe(false)
  })

  it('按可信证据解除隔离，只改变资格和事件版本', () => {
    const example = createExample('verified')
    const result = performAction(example, command(example, 'correct_source'), true, at, id)
    if ('code' in result) throw new Error(result.code)
    expect(result.example.issue).toMatchObject({
      status: 'resolved',
      disputed: false,
      current_revision: example.issue.current_revision,
      watermark: example.issue.watermark,
      qualification_version: example.issue.qualification_version + 1,
      event_version: example.issue.event_version + 1,
      candidate_revisions: [],
      evidence: {},
    })
    expect(result.example.observation.revisions).toEqual(example.observation.revisions)
  })

  it('拒绝理由缺失、过期版本、无裁决权限和无证明的订正', () => {
    const example = createExample('unordered')
    const claim = command(example, 'claim')
    expect(performAction(example, { ...claim, reason_code: '' }, true, at, id)).toEqual({ code: 'invalid_request' })
    expect(performAction(example, { ...claim, expected_version: 0 }, true, at, id)).toEqual({ code: 'precondition_conflict' })
    expect(performAction(example, claim, false, at, id)).toEqual({ code: 'permission_denied' })
    expect(performAction(example, command(example, 'correct_source'), true, at, id)).toEqual({ code: 'mapping_evidence_mismatch' })
    expect(example.issue.status).toBe('open')
  })

  it('领取后的保留动作继续隔离，拒绝旧版本处置', () => {
    const example = createExample('unordered')
    const claim = performAction(example, command(example, 'claim'), true, at, id)
    if ('code' in claim) throw new Error(claim.code)
    expect(claim.example.issue.status).toBe('in_review')
    expect(isActionAvailable(claim.example, 'claim')).toBe(false)
    expect(performAction(claim.example, command(example, 'hold'), true, at, id)).toEqual({ code: 'precondition_conflict' })
    const hold = performAction(claim.example, command(claim.example, 'hold'), true, at, id)
    if ('code' in hold) throw new Error(hold.code)
    expect(hold.example.issue.disputed).toBe(true)
    expect(hold.example.localActions.map(({ response }) => response.outcome)).toEqual(['claim', 'hold'])
  })
})
