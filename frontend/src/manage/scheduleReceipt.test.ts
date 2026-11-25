// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'
import Collection from './Collection.vue'
import { beginSchedule, pendingSchedule } from './scheduleReceipt'
import type { ScheduleCreateCommand } from './scheduleContract'

const command: ScheduleCreateCommand = {
  action: 'create_draft', expected_version: 3, request_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  reason_code: 'schedule_create', template: 7, cadence_seconds: 3600, first_due_at: '2027-01-02T10:00:00Z',
}
const directory = { results: [{ template: 7, job: null, source: 'local', product: 2, template_version: 3, allowed_cadences: [3600], plan_state: 'unconfigured' }], next_cursor: null }
const receipt = { action: 'create_draft', action_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', job: 19, version: 1, state: 'draft', recorded_at: '2026-11-25T10:00:00Z' }

async function openPending() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.capabilities = { workspaces: ['user', 'manage'], actions: ['configure_collection_schedule'] }
  session.enterScope('synthetic')
  const envelope = beginSchedule(command, null)
  const wrapper = mount(Collection, { global: { plugins: [pinia], stubs: { CollectionLedger: true, ElIcon: true } } })
  await flushPromises()
  const lookup = () => wrapper.get('[aria-label="待核对的计划操作"] button').trigger('click')
  return { wrapper, lookup, envelope }
}

afterEach(() => { api.clearPrivateState(false); vi.restoreAllMocks() })

describe('原计划回执查账', () => {
  it('404、错动作和坏数据仍保留原信封，准确回执才解除待核', async () => {
    vi.spyOn(api, 'get').mockResolvedValue(directory)
    const post = vi.spyOn(api, 'post').mockRejectedValueOnce(new ApiError('', 404, 'not_found'))
      .mockResolvedValueOnce({ ...receipt, action: 'activate' })
      .mockResolvedValueOnce({ ...receipt, job: 0 }).mockResolvedValueOnce(receipt)
    const { wrapper, lookup, envelope } = await openPending()
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        await lookup()
        await flushPromises()
        expect(pendingSchedule.value).toBe(envelope)
        expect(wrapper.text()).not.toContain('已确认原操作')
        expect(beginSchedule({ ...command, request_id: crypto.randomUUID() }, null)).toBeUndefined()
      }
      await lookup()
      await flushPromises()
      expect(pendingSchedule.value).toBeNull()
      expect(wrapper.text()).toContain('已确认原操作：Job 19')
      expect(post.mock.calls).toHaveLength(4)
      for (const [path, body] of post.mock.calls) {
        expect(path).toBe('admin/command-receipts/lookup/')
        expect(body).toBe(envelope)
      }
    } finally { wrapper.unmount() }
  })

  it('会话清除时丢弃在途查账结果', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue(directory)
    let finish!: (value: unknown) => void
    const post = vi.spyOn(api, 'post').mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const { wrapper, lookup } = await openPending()
    try {
      await lookup()
      api.clearPrivateState(false)
      expect(post.mock.calls[0]?.[2]?.aborted).toBe(true)
      finish(receipt)
      await flushPromises()
      expect(get).toHaveBeenCalledTimes(1)
      expect(pendingSchedule.value).toBeNull()
      expect(wrapper.text()).not.toContain('Job 19')
    } finally { wrapper.unmount() }
  })
})
