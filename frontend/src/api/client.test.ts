// @vitest-environment jsdom
import { effectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiPath, decodeJson, usePrivateRead } from './client'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('axios', async importOriginal => {
  const original = await importOriginal<typeof import('axios')>()
  return { ...original, default: { ...original.default, create: () => ({ request }) } }
})

afterEach(() => {
  api.clearPrivateState(false)
  request.mockReset()
})

describe('API boundary', () => {
  it('preserves safe integers and refuses IDs that JavaScript cannot represent exactly', () => {
    expect(decodeJson('{"id":9007199254740991}')).toEqual({ id: Number.MAX_SAFE_INTEGER })
    expect(() => decodeJson('{"id":9007199254740993}')).toThrowError(/不能精确表示/)
    expect(() => decodeJson('<html>error</html>')).toThrowError(/预期的数据格式/)
  })

  it('only accepts relative same-origin API paths', () => {
    expect(apiPath('auth/csrf/')).toBe('auth/csrf/')
    expect(() => apiPath('https://outside.example/api/')).toThrow()
    expect(() => apiPath('../admin/')).toThrow()
    expect(() => apiPath('auth//login/')).toThrow()
  })

  it.each([
    { method: 'POST', path: 'admin/history-imports/', status: 503, code: 'dispatch_unconfirmed', id: '2843a377-a9a9-46b8-9194-34c4b8c2dce0', exposes: true },
    { method: 'POST', path: 'admin/history-imports/', status: 503, code: 'dispatch_unconfirmed', id: '../outside', exposes: false },
    { method: 'POST', path: 'admin/history-imports/', status: 503, code: 'database_unavailable', id: '2843a377-a9a9-46b8-9194-34c4b8c2dce0', exposes: false },
    { method: 'POST', path: 'admin/history-imports/', status: 403, code: 'dispatch_unconfirmed', id: '2843a377-a9a9-46b8-9194-34c4b8c2dce0', exposes: false },
    { method: 'GET', path: 'admin/history-imports/', status: 503, code: 'dispatch_unconfirmed', id: '2843a377-a9a9-46b8-9194-34c4b8c2dce0', exposes: false },
    { method: 'POST', path: 'admin/collection-runs/', status: 503, code: 'dispatch_unconfirmed', id: '2843a377-a9a9-46b8-9194-34c4b8c2dce0', exposes: false },
  ])('exposes only a valid import receipt for $method $path $status $code ($id)', async ({ method, path, status, code, id, exposes }) => {
    const headers = { 'content-type': 'application/json' }
    if (method === 'POST') request.mockResolvedValueOnce({ status: 200, headers, data: '{"csrf_token":"test"}' })
    request.mockRejectedValueOnce({ isAxiosError: true, response: {
      status, headers, data: JSON.stringify({ code, attempt_id: id, raw_value: 'hidden' }),
    } })
    const result = method === 'POST' ? api.post(path, {}) : api.get(path)
    await expect(result).rejects.toMatchObject({ status, code, attemptId: exposes ? id : undefined })
  })

  it('discards a superseded private response without replacing the latest result', async () => {
    const scope = effectScope()
    const read = scope.run(() => usePrivateRead<number>())!
    let resolveOld!: (value: number) => void
    const old = read.run(() => new Promise<number>((resolve) => { resolveOld = resolve }))
    const current = await read.run(async () => 2)
    resolveOld(1)
    expect(await old).toBeUndefined()
    expect(current).toBe(2)
    expect(read.data.value).toBe(2)
    scope.stop()
  })

  it('clears private data and cancels its in-flight read on explicit invalidation', async () => {
    const scope = effectScope()
    const read = scope.run(() => usePrivateRead<number>())!
    let signal: AbortSignal | undefined
    let resolve!: (value: number) => void
    const pending = read.run((requestSignal) => {
      signal = requestSignal
      return new Promise<number>((complete) => { resolve = complete })
    })
    read.clear()
    resolve(9)
    expect(signal?.aborted).toBe(true)
    expect(read.data.value).toBeNull()
    expect(read.error.value).toBeNull()
    expect(read.busy.value).toBe(false)
    scope.stop()
    expect(await pending).toBeUndefined()
  })
})
