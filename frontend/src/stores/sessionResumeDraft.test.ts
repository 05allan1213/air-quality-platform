import { afterEach, expect, it, vi } from 'vitest'
import { createSessionResumeDraft, resumeDraftKey } from './sessionResumeDraft'

afterEach(() => vi.useRealTimers())

it('freezes a one-time condition snapshot and releases it only after completing the current check', () => {
  const drafts = createSessionResumeDraft()
  const route = {}
  const key = resumeDraftKey(route, 'reader', 'synthetic')
  const indicators = ['CO']
  const unregister = drafts.register(key, () => ({ mode: 'range', indicators }))
  const token = drafts.stage(key)
  unregister()
  indicators.push('NO2')

  expect(drafts.take(key)).toBeNull()
  expect(drafts.stage(key)).toBe(token)
  drafts.complete(Symbol(), key)
  expect(drafts.take(key)).toBeNull()
  drafts.complete(token, resumeDraftKey(route, 'reader', 'synthetic'))
  expect(drafts.take(key)).toEqual({ mode: 'range', indicators: ['CO'] })
  expect(drafts.take(key)).toBeNull()
})

it('drops on an unverified, different-account, changed-scope, or replaced route', () => {
  const drafts = createSessionResumeDraft()
  const route = {}
  const key = resumeDraftKey(route, 'reader', 'synthetic')
  drafts.register(key, () => ({ mode: 'range' }))

  for (const changed of [undefined, resumeDraftKey(route, 'peer', 'synthetic'), resumeDraftKey(route, 'reader', ''), resumeDraftKey({}, 'reader', 'synthetic')]) {
    drafts.complete(drafts.stage(key), changed)
    expect(drafts.take(key)).toBeNull()
  }
  const token = drafts.stage(key)
  drafts.discard()
  drafts.complete(token, key)
  expect(drafts.take(key)).toBeNull()
})

it('expires the draft while the tab is suspended', () => {
  vi.useFakeTimers()
  const drafts = createSessionResumeDraft()
  const key = resumeDraftKey({}, 'reader', '')
  const unregister = drafts.register(key, () => ({ mode: 'range' }))
  const token = drafts.stage(key)
  unregister()
  // Wall time still advances while the browser suspends the timer callback.
  vi.setSystemTime(Date.now() + 31_000)
  drafts.complete(token, key)
  expect(drafts.take(key)).toBeNull()
  expect(drafts.stage(key)).toBeUndefined()
})
