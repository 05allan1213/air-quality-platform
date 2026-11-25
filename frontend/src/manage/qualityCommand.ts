import { onScopeDispose, ref, shallowReactive } from 'vue'
import { ApiError, api, privateStateGeneration } from '../api/client'
import { issueStates, requestId, runId } from './qualityContract'

type Command = {
  action: string
  reason_code: string
  expected_version: number
  evidence_ref: string | null
  request_id: string
}

export type CommandReceipt = { action_id: string; state: string; version: number; action: string }
type PendingCommand = { target: number; family: 'quality' | 'station_change'; command: Command }

function requireReceipt<T>(result: T, original: PendingCommand, lookup = false) {
  const body = result as Record<string, unknown> | null | undefined
  const state = body?.[lookup ? 'state' : 'status']
  const version = original.family === 'quality' && !lookup ? body?.event_version : body?.version
  const states = original.family === 'quality' ? Object.keys(issueStates) : ['applied', 'mapping_review_required']
  if (!runId(body?.action_id) || typeof state !== 'string' || !states.includes(state) ||
      version !== original.command.expected_version + 1 || (lookup && body?.action !== original.command.action)) {
    throw new ApiError('服务未返回可核验的原始回执。', 502, 'invalid_response')
  }
  return result
}

// In-memory only, so returning to the page in the same tab cannot silently retry
// a command whose response was lost. Evidence references never go into storage.
const pending = shallowReactive(new Map<string, PendingCommand>())
const clearPending = () => pending.clear()
window.addEventListener('air-quality:clear-reads', clearPending)

export function useQualityCommand(family: PendingCommand['family']) {
  const busy = ref(false)
  let controller: AbortController | undefined
  const key = (target: number) => `${family}:${target}`

  async function execute<T>(work: (signal: AbortSignal) => Promise<T>, settled: (cause?: unknown) => void) {
    const active = new AbortController()
    const generation = privateStateGeneration()
    controller = active
    busy.value = true
    const current = () => !active.signal.aborted && generation === privateStateGeneration()
    try {
      const result = await work(active.signal)
      if (current()) {
        settled()
        return result
      }
    } catch (cause) {
      if (current()) {
        settled(cause)
        throw cause
      }
    } finally {
      if (controller === active) {
        controller = undefined
        busy.value = false
      }
    }
  }

  async function submit<T>(target: number, path: string, body: Omit<Command, 'request_id'>) {
    const id = key(target)
    if (busy.value || pending.has(id)) return
    const command = { ...body, request_id: requestId() }
    const original = { family, target, command }
    pending.set(id, original)
    return execute(async signal => {
      return requireReceipt(await api.post<T>(path, command, signal), original)
    }, cause => {
      const rejected = cause instanceof ApiError && [400, 403, 404, 409].includes(cause.status) && cause.code !== 'request_failed'
      if (pending.get(id) === original && (cause === undefined || rejected)) pending.delete(id)
    })
  }

  async function lookup(target: number) {
    const id = key(target)
    const original = pending.get(id)
    if (busy.value || !original) return
    return execute(async signal => {
      // This POST is a read-only lookup; it never resubmits the original action.
      const result = await api.post<CommandReceipt>('admin/command-receipts/lookup/', {
        family: original.family,
        target: original.target,
        command: original.command,
      }, signal)
      return requireReceipt(result, original, true)
    }, cause => {
      if (cause === undefined && pending.get(id) === original) pending.delete(id)
    })
  }

  function cancel() {
    controller?.abort()
    controller = undefined
    busy.value = false
  }
  window.addEventListener('air-quality:clear-reads', cancel)
  onScopeDispose(() => {
    window.removeEventListener('air-quality:clear-reads', cancel)
    cancel()
  })
  return { busy, submit, lookup, pendingId: (target: number) => pending.get(key(target))?.command.request_id }
}
