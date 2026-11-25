// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import CollectionRunHistoryImport from './CollectionRunHistoryImport.vue'

const stage = '87444c12-efb8-4f11-a179-f644e2265038'
const id = '2843a377-a9a9-46b8-9194-34c4b8c2dce0'
const lookup = `admin/history-imports/${id}/`

async function submit(wrapper: VueWrapper) {
  await wrapper.get('input').setValue(stage)
  await wrapper.get('form').trigger('submit')
  await wrapper.get('.history-confirm .primary').trigger('click')
}

function showImport() {
  return mount(CollectionRunHistoryImport, { props: { canImport: true } })
}

afterEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('历史导入回执恢复', () => {
  it('503 已受理但投递未确认，按原 attempt GET 并阻止未判明时重复提交', async () => {
    const post = vi.spyOn(api, 'post').mockRejectedValue(new ApiError('', 503, 'dispatch_unconfirmed', undefined, id))
    const get = vi.spyOn(api, 'get').mockResolvedValue({ id, state: 'pending', dispatch: 'unconfirmed' })
    const wrapper = showImport()
    try {
      await submit(wrapper)
      await flushPromises()
      expect(wrapper.text()).toContain('受理时投递未确认')
      expect(wrapper.text()).not.toContain('已完成')
      expect(get.mock.calls.map(([path]) => path)).toEqual([lookup])
      const original = wrapper.findAll('button').find(button => button.text() === '查原尝试状态')!
      await original.trigger('click')
      await flushPromises()
      expect(get.mock.calls.map(([path]) => path)).toEqual([lookup, lookup])
      expect(wrapper.get('input').attributes('disabled')).toBeDefined()
      get.mockResolvedValueOnce({ id, state: 'pending', dispatch: 'sent' })
      await original.trigger('click')
      await flushPromises()
      expect(get.mock.calls.map(([path]) => path)).toEqual([lookup, lookup, lookup])
      expect(wrapper.get('input').attributes('disabled')).toBeUndefined()
      expect(wrapper.text()).not.toContain('已完成')
      expect(post).toHaveBeenCalledTimes(1)
    } finally {
      wrapper.unmount()
    }
  })

  it('无 ID 的未知结果返回路由后仍锁定，浏览器仅保留非标识标记', async () => {
    const post = vi.spyOn(api, 'post').mockRejectedValue(new ApiError('', 0, 'network_error'))
    vi.spyOn(api, 'get').mockResolvedValue({ results: [], page: { next_cursor: null } })
    const first = showImport()
    await submit(first)
    await flushPromises()
    first.unmount()
    expect(Object.entries(sessionStorage)).toEqual([['aq-history-import-pending-v1', '1']])
    const second = showImport()
    try {
      expect(second.get('input').attributes('disabled')).toBeDefined()
      expect(second.text()).toContain('原导入结果待核')
      await second.get('form').trigger('submit')
      expect(second.find('.history-confirm').exists()).toBe(false)
      expect(post).toHaveBeenCalledTimes(1)
    } finally {
      second.unmount()
    }
  })

  it('代次清理丢弃迟到的原尝试回执，保留提交护栏', async () => {
    let fail!: (error: ApiError) => void
    const post = vi.spyOn(api, 'post').mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    const get = vi.spyOn(api, 'get')
    const wrapper = showImport()
    try {
      await submit(wrapper)
      api.clearPrivateState(false)
      fail(new ApiError('', 503, 'dispatch_unconfirmed', undefined, id))
      await flushPromises()
      expect(post.mock.calls[0][2]?.aborted).toBe(true)
      expect(wrapper.text()).not.toContain(id)
      expect(wrapper.get('input').attributes('disabled')).toBeDefined()
      expect(get).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })
})
