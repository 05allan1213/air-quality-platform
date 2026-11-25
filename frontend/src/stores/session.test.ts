// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://localhost/app/login"}
import { createPinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { useSessionStore } from './session'

vi.mock('../api/client', () => ({
  ApiError: class extends Error {},
  api: { post: vi.fn(), get: vi.fn(), clearPrivateState: vi.fn(), notifyLogoutState: vi.fn() },
}))

it('keeps a shared logout lock when local storage is readable but rejects writes', async () => {
  const setItem = Storage.prototype.setItem
  const storageSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
    if (this === localStorage && key === 'aq:logout-pending') throw new DOMException('Quota exceeded', 'QuotaExceededError')
    setItem.call(this, key, value)
  })
  try {
    vi.mocked(api.post).mockRejectedValueOnce(new Error('Undelivered request')).mockResolvedValueOnce(undefined)
    vi.mocked(api.get).mockResolvedValueOnce({ username: 'fixture' })
    const initiating = useSessionStore(createPinia())
    const existing = useSessionStore(createPinia())

    expect(await initiating.signOut()).toBe(false)
    expect(localStorage.getItem('aq:logout-pending')).toBeNull()
    expect(document.cookie).toBe('aq_logout_pending=1')
    expect(useSessionStore(createPinia()).logoutPending).toBe(true)
    window.dispatchEvent(new Event('air-quality:logout-state'))
    expect(existing.logoutPending).toBe(true)

    expect(await initiating.signOut()).toBe(true)
    expect(document.cookie).toBe('')
    window.dispatchEvent(new Event('air-quality:logout-state'))
    expect(existing.logoutPending).toBe(false)
    expect(useSessionStore(createPinia()).logoutPending).toBe(false)
  } finally {
    storageSpy.mockRestore()
  }
})

const person = { username: 'fixture', first_name: '', last_name: '' }
const access = { workspaces: ['user', 'manage'], actions: ['manage_collection'] }

function deferred() {
  let resolve!: (value: unknown) => void
  const promise = new Promise(resolvePromise => { resolve = resolvePromise })
  return { promise, resolve }
}

it('coalesces concurrent verification and only exposes the refreshed capabilities', async () => {
  const store = useSessionStore(createPinia())
  const capabilities = deferred()
  vi.mocked(api.get).mockResolvedValueOnce(person).mockResolvedValueOnce(access)
  await store.ensureLoaded()
  store.enterScope('synthetic')
  const generationBefore = vi.mocked(api.clearPrivateState).mock.calls.length
  const getCount = vi.mocked(api.get).mock.calls.length
  vi.mocked(api.get).mockResolvedValueOnce(person).mockReturnValueOnce(capabilities.promise)
  try {
    const verify = store.ensureLoaded(true)
    const navigate = store.ensureLoaded()
    const focus = store.ensureLoaded(true)
    expect(store.authenticated).toBe(true)
    expect(store.verified).toBe(false)
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(getCount + 2))
    capabilities.resolve({ workspaces: ['user'], actions: [] })
    await Promise.all([verify, navigate, focus])
    expect(store.verified).toBe(true)
    expect(store.scope).toBe('synthetic')
    expect(store.capabilities.actions).toEqual([])
    expect(api.clearPrivateState).toHaveBeenCalledTimes(generationBefore)
  } finally {
    store.$dispose()
  }
})

it('keeps the private shell closed after a temporary failure and verifies again on retry', async () => {
  const store = useSessionStore(createPinia())
  vi.mocked(api.get).mockResolvedValueOnce(person).mockResolvedValueOnce(access)
  await store.ensureLoaded()
  vi.mocked(api.get).mockRejectedValueOnce(new Error('server unavailable')).mockResolvedValueOnce(access)
  try {
    await expect(store.ensureLoaded(true)).rejects.toThrow('server unavailable')
    expect(store.verificationFailed).toBe(true)
    expect(store.verified).toBe(false)
    expect(store.profile).toEqual(person)
    vi.mocked(api.get).mockResolvedValueOnce(person).mockResolvedValueOnce(access)
    await store.ensureLoaded()
    expect(store.verified).toBe(true)
    expect(store.verificationFailed).toBe(false)
  } finally {
    store.$dispose()
  }
})

it('ignores late verification after logout and skips verification while logout is unconfirmed', async () => {
  const store = useSessionStore(createPinia())
  const profile = deferred()
  const capabilities = deferred()
  const getCount = vi.mocked(api.get).mock.calls.length
  vi.mocked(api.get).mockReturnValueOnce(profile.promise).mockReturnValueOnce(capabilities.promise)
  try {
    const verify = store.ensureLoaded(true)
    await vi.waitFor(() => expect(api.get).toHaveBeenCalledTimes(getCount + 2))
    const signal = vi.mocked(api.get).mock.lastCall?.[2]
    localStorage.setItem('aq:logout-pending', '1')
    await store.ensureLoaded(true)
    expect(store.logoutPending).toBe(true)
    expect(signal?.aborted).toBe(true)
    profile.resolve(person)
    capabilities.resolve(access)
    await verify
    expect(store.verified).toBe(false)
    expect(store.profile).toBeNull()
    expect(store.capabilities.actions).toEqual([])
    expect(api.get).toHaveBeenCalledTimes(getCount + 2)
  } finally {
    localStorage.removeItem('aq:logout-pending')
    store.$dispose()
  }
})

it('clears the previous private state when the server session belongs to a different account', async () => {
  const store = useSessionStore(createPinia())
  vi.mocked(api.get).mockResolvedValueOnce(person).mockResolvedValueOnce(access)
  await store.ensureLoaded()
  vi.mocked(api.get).mockResolvedValueOnce({ ...person, username: 'other-fixture' }).mockResolvedValueOnce(access)
  try {
    await store.ensureLoaded(true)
    expect(store.profile).toBeNull()
    expect(store.capabilities.actions).toEqual([])
    expect(store.verified).toBe(false)
    expect(api.clearPrivateState).toHaveBeenLastCalledWith(false)
  } finally {
    store.$dispose()
  }
})
