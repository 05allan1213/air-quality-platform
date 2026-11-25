// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { routerKey } from 'vue-router'
import { ApiError, api } from '../api/client'
import { useSessionStore } from '../stores/session'
import CollectionLedger from './CollectionLedger.vue'
import OperationsGrants from './OperationsGrants.vue'
import { usePendingManagementCommands } from './pendingManagementCommands'

const requestId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const resultId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const recordedAt = '2024-02-01T02:00:00Z'
const entry = { job: 12, product: 13, target: recordedAt, reason: 'manual' }
const grantCommand = { family: 'grant_withdraw', target: 19, command: {
  action: 'withdraw', reason_code: 'terms_ended', expected_version: 2, request_id: requestId,
} } as const

function signIn(actions: string[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.profile = { username: 'fixture', first_name: '', last_name: '' }
  session.capabilities = { workspaces: ['user', 'manage'], actions }
  session.enterScope('synthetic')
  return { pinia, session, pending: usePendingManagementCommands() }
}

afterEach(() => vi.restoreAllMocks())

const collectionCommands = [
  { family: 'job_control', target: 12, command: {
    action: 'pause', expected_version: 2, request_id: requestId, reason_code: 'operator_pause',
  } },
  { family: 'redeliver', target: resultId, command: {
    action: 'redeliver', expected_version: 2, request_id: requestId, reason_code: 'operator_recovery',
  } },
] as const

describe('管理命令待核', () => {
  it.each(collectionCommands)('保留 $family 的原信封并限制数据域与身份', envelope => {
    const { pinia, session, pending } = signIn(['manage_collection', 'manage_grants'])
    try {
      const original = { scope: 'synthetic', envelope } as const
      expect(pending.beginCollection(original)).toBe(true)
      expect(pending.beginGrant(grantCommand)).toBe(true)
      session.enterScope('')
      expect(pending.collection).toBe(original)
      expect(pending.beginCollection({ ...original, scope: '' })).toBe(false)
      expect(pending.finishCollection({ ...original })).toBe(false)
      expect(pending.grant).toBe(grantCommand)
      session.enterScope('synthetic')
      expect(pending.collection?.envelope).toBe(envelope)
      session.capabilities.actions = ['manage_grants']
      expect(pending.collection).toBeNull()
      expect(pending.grant).toBe(grantCommand)
      session.profile = { username: '另一账号', first_name: '', last_name: '' }
      expect(pending.grant).toBeNull()
    } finally { disposePinia(pinia) }
  })

  it('接受中断后更换产品筛选，查账 404 和错动作都保留原命令', async () => {
    const { pinia, session, pending } = signIn(['run_collection'])
    vi.spyOn(api, 'get').mockResolvedValue({ results: [entry] })
    const result = { run_id: resultId, window_id: 4, state: 'submitted', action: 'manual', recorded_at: recordedAt, dispatch: 'sent', status: 'pending' }
    const post = vi.spyOn(api, 'post').mockRejectedValueOnce(new ApiError('', 503, 'dispatch_unconfirmed'))
      .mockRejectedValueOnce(new ApiError('', 404, 'not_found'))
      .mockResolvedValueOnce({ ...result, action: 'backfill' }).mockResolvedValueOnce(result)
    const mountLedger = (productId?: number) => mount(CollectionLedger, {
      props: { productId }, global: { plugins: [pinia], provide: { [routerKey]: {} }, stubs: { ElIcon: true } },
    })
    let ledger = mountLedger(entry.product)
    const button = (label: string) => ledger.findAll('button').find(node => node.text() === label)!
    const select = async () => {
      await ledger.get('form[aria-label="获权执行目标"] input').setValue(entry.target)
      await ledger.get('form[aria-label="获权执行目标"]').trigger('submit')
      await flushPromises()
      await button(`Job ${entry.job} · 产品 ${entry.product}`).trigger('click')
    }
    try {
      await select()
      await button('提交合成采集').trigger('click')
      await button('确认提交').trigger('click')
      await flushPromises()
      const original = pending.collection
      expect(original?.envelope.command).toMatchObject({ job: entry.job, target: entry.target, reason: entry.reason })
      ledger.unmount()
      session.enterScope('')
      ledger = mountLedger()
      expect(ledger.text()).toContain('另一数据域仍有原采集命令待核')
      expect(ledger.text()).not.toContain(original!.envelope.command.request_id)
      session.enterScope('synthetic')
      await flushPromises()
      await select()
      for (let attempt = 0; attempt < 2; attempt++) {
        expect(button('提交合成采集').attributes('disabled')).toBeDefined()
        await button('查本人执行回执').trigger('click')
        await flushPromises()
        expect(pending.collection).toBe(original)
        expect(ledger.text()).not.toContain('采集受理回执')
      }
      await button('查本人执行回执').trigger('click')
      await flushPromises()
      expect(ledger.text()).toContain('采集受理回执')
      expect(pending.collection).toBeNull()
      expect(post.mock.calls.map(([path, body]) => [path, body])).toEqual([
        ['admin/collection-runs/', original!.envelope.command],
        ...Array.from({ length: 3 }, () => ['admin/command-receipts/lookup/', original!.envelope]),
      ])
    } finally { ledger.unmount(); disposePinia(pinia) }
  })

  it('许可交接编辑后锁定新提交，会话清除时丢弃迟到回执', async () => {
    const { pinia, session, pending } = signIn(['manage_grants'])
    expect(pending.beginGrant(grantCommand)).toBe(true)
    vi.spyOn(api, 'get').mockResolvedValue({ product: 10, grant: 20, version: 2, state: 'registered' })
    let finish!: (value: unknown) => void
    const result = { action: 'withdraw', action_id: resultId, version: 3, state: 'withdrawn', recorded_at: recordedAt }
    const post = vi.spyOn(api, 'post').mockRejectedValueOnce(new ApiError('', 404, 'not_found'))
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const grants = mount(OperationsGrants, { global: { plugins: [pinia] } })
    const check = () => grants.findAll('button').find(node => node.text() === '查询本人撤回回执')!.trigger('click')
    try {
      const handoff = grants.get('form.handoff-form')
      await handoff.findAll('input')[0].setValue('10')
      await handoff.findAll('input')[1].setValue('20')
      await handoff.trigger('submit')
      await flushPromises()
      const form = grants.get('form.withdraw-form')
      await form.get('select').setValue('terms_ended')
      await form.get('input').setValue(true)
      expect(form.get('button').attributes('disabled')).toBeDefined()
      await form.trigger('submit')
      expect(post).not.toHaveBeenCalled()
      await check()
      await flushPromises()
      expect(pending.grant).toBe(grantCommand)
      expect(grants.text()).not.toContain('许可已撤回')
      await check()
      expect(post.mock.calls.map(([path, body]) => [path, body])).toEqual([
        ['admin/command-receipts/lookup/', grantCommand], ['admin/command-receipts/lookup/', grantCommand],
      ])
      session.clearPrivate(false)
      finish(result)
      await flushPromises()
      expect(post.mock.calls[1][2]?.aborted).toBe(true)
      expect(pending.grant).toBeNull()
      expect(grants.text()).not.toContain(resultId)
    } finally { grants.unmount(); disposePinia(pinia) }
  })
})
