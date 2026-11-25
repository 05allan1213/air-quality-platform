// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'
import CollectionLedger from './CollectionLedger.vue'
import CollectionRunDetail from './CollectionRunDetail.vue'
import {
  buildHistoryImportCommand, buildJobControlCommand, isUuid, knownCollectionRejection,
  ledgerPath, validDateSpan, validSubmissionReceipt, validTarget,
} from './collectionLedger'

const runId = '2843a377-a9a9-46b8-9194-34c4b8c2dce0'
const target = '2024-02-02T00:00:00Z'

afterEach(() => vi.restoreAllMocks())

async function mountLedger(actions: string[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.profile = { username: 'fixture', first_name: '', last_name: '' }
  session.capabilities = { workspaces: ['user', 'manage'], actions }
  session.enterScope('synthetic')
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/', name: 'manage-quality', component: { template: '<div />' },
  }] })
  await router.push('/')
  return mount(CollectionLedger, { global: { plugins: [pinia, router] } })
}

describe('采集账本契约', () => {
  it('限制来源日跨度并按 UTC 核验目标整点和日期', () => {
    expect(validDateSpan('2024-02-01', '2024-03-02')).toBe(true)
    expect(validDateSpan('2024-02-01', '2024-03-03')).toBe(false)
    expect(validDateSpan('2024-02-30', '2024-03-03')).toBe(false)
    expect(validTarget('2024-02-02T08:30:00+08:30')).toBe(true)
    expect(validTarget('2024-02-02T08:00:00+08:30')).toBe(false)
    expect(validTarget('2024-02-30T00:00:00Z')).toBe(false)
    expect(validTarget('2024-02-02T00:00:00')).toBe(false)
  })

  it('只沿预期端点路径读取并保留服务端条件', () => {
    const href = '/api/v1/admin/collection-windows/?scope=synthetic&product=8'
    expect(ledgerPath(href, 'admin/collection-windows/')).toBe(href.slice('/api/v1/'.length))
    expect(ledgerPath('//untrusted.example/api/v1/admin/collection-windows/', 'admin/collection-windows/')).toBeUndefined()
    expect(ledgerPath('/api/v1/admin/collection-windows/2/', 'admin/collection-windows/')).toBeUndefined()
  })

  it('固定控制版本和暂存导入契约，并验证原 Run 回执', () => {
    const command = buildJobControlCommand({ type: 'job', id: 2, state: 'active', control_version: 7 }, 'pause')
    expect(command).toMatchObject({ action: 'pause', expected_version: 7, reason_code: 'operator_pause' })
    expect(isUuid(command.request_id)).toBe(true)
    expect(buildHistoryImportCommand(runId)).toEqual({ profile: 'uci501-edges-v1', reason: 'historical_verification', stage_id: runId })
    expect(validSubmissionReceipt({ run_id: runId, window_id: 11 }, runId)).toBe(true)
    expect(validSubmissionReceipt({ run_id: runId, window_id: 0 })).toBe(false)
    expect(validSubmissionReceipt({ run_id: runId, window_id: 11 }, command.request_id)).toBe(false)
    expect(knownCollectionRejection(new ApiError('', 409, 'precondition_conflict'))).toBe(true)
    expect(knownCollectionRejection(new ApiError('', 503, 'dispatch_unconfirmed'))).toBe(false)
  })

  it('执行者超时后仅查原命令，获本人回执后也没有账本入口', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ results: [{ product: 8, job: 2, reason: 'manual', target }], next_cursor: null })
    const post = vi.spyOn(api, 'post')
      .mockRejectedValueOnce(new ApiError('', 0, 'network_error'))
      .mockResolvedValueOnce({ run_id: runId, window_id: 11, dispatch: 'unconfirmed', status: 'pending', state: 'submitted', action: 'manual', recorded_at: target })
    const wrapper = await mountLedger(['run_collection'])
    try {
      await wrapper.get('input[type="text"]').setValue(target)
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      const button = (text: string) => wrapper.findAll('button').find(item => item.text() === text)!
      await button('Job 2 · 产品 8').trigger('click')
      await button('提交合成采集').trigger('click')
      await button('确认提交').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('采集受理结果待核')
      expect(button('查看账本')).toBeUndefined()
      await button('查本人执行回执').trigger('click')
      await flushPromises()
      expect(post).toHaveBeenCalledTimes(2)
      const command = post.mock.calls[0][1]
      expect(post.mock.calls[0][0]).toBe('admin/collection-runs/')
      expect(post.mock.calls[1]).toEqual(['admin/command-receipts/lookup/', { family: 'manual_run', target: 2, command }, expect.any(AbortSignal)])
      expect(wrapper.text()).toContain(runId)
      expect(button('查看原 Run')).toBeUndefined()
      expect(get.mock.calls.map(([path]) => path)).toEqual(['admin/catalog/products/'])
    } finally {
      wrapper.unmount()
    }
  })

  it('Job 恢复响应丢失时不再执行控制动作，查账使用原 Job 入口及版本', async () => {
    const get = vi.spyOn(api, 'get')
      .mockResolvedValueOnce({ results: [{ id: 2, product: 8, synthetic: true, state: 'paused' }], page: { next_cursor: null } })
      .mockResolvedValue({ job: 2, targets: {
        job: { type: 'job', id: 2, state: 'paused', control_version: 7 },
        source: { type: 'source', id: 4, state: 'active', control_version: 0 },
        product: { type: 'product', id: 8, state: 'active', control_version: 0 },
      } })
    const post = vi.spyOn(api, 'post')
      .mockRejectedValueOnce(new ApiError('', 503, 'database_unavailable'))
      .mockResolvedValueOnce({ state: 'active', action: 'resume', recorded_at: target, action_id: runId, version: 8 })
    const wrapper = await mountLedger(['manage_collection'])
    try {
      const button = (text: string) => wrapper.findAll('button').find(item => item.text() === text)!
      await button('读取获权 Job').trigger('click')
      await flushPromises()
      await button('Job 2 · 产品 8 · 已暂停').trigger('click')
      await flushPromises()
      await button('恢复 Job').trigger('click')
      await button('确认提交').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('原控制命令待核')
      await button('重新读取控制状态').trigger('click')
      await flushPromises()
      expect(button('恢复 Job').attributes('disabled')).toBeDefined()
      await button('查本人控制回执').trigger('click')
      await flushPromises()
      expect(post).toHaveBeenCalledTimes(2)
      const command = post.mock.calls[0][1]
      expect(command).toMatchObject({ action: 'resume', expected_version: 7, reason_code: 'operator_resume' })
      expect(post.mock.calls[1]).toEqual(['admin/command-receipts/lookup/', { family: 'job_control', target: 2, command }, expect.any(AbortSignal)])
      expect(get.mock.calls.at(-1)?.[0]).toBe('admin/collection-jobs/2/')
      expect(wrapper.text()).toContain('已查回原命令的本人回执')
    } finally {
      wrapper.unmount()
    }
  })

  it('部分授权仅呈现获准的窗口覆盖，不把未披露的结果显示成无', () => {
    const wrapper = mount(CollectionRunDetail, { props: { projection: {
      window_id: 11, state: 'partially_authorized', result: null, coverage: { E: 4, O: 1, V: 0 },
    } }, global: { stubs: { ElIcon: true } } })
    try {
      expect(wrapper.get('dl').text()).toContain('窗口结果未披露')
      expect(wrapper.get('dl').text()).toContain('O 实到1V 有效0')
      expect(wrapper.text()).toContain('只显示当前获权部分')
    } finally {
      wrapper.unmount()
    }
  })
})
