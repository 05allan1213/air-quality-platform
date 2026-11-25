import { defineStore } from 'pinia'
import { shallowRef, watch } from 'vue'
import { ApiError } from '../api/client'
import { type DataScope, useSessionStore } from '../stores/session'
import { isUuid, knownCollectionRejection, type CollectionCommandLookup, type GovernanceReceipt } from './collectionLedger'

export type GrantWithdrawCommand = {
  action: 'withdraw'
  reason_code: 'terms_ended' | 'retention_withdrawn' | 'rights_review'
  expected_version: number
  request_id: string
}

type CollectionPending =
  | { scope: DataScope; envelope: Extract<CollectionCommandLookup, { family: 'manual_run' }>; product: number }
  | { scope: DataScope; envelope: Exclude<CollectionCommandLookup, { family: 'manual_run' }>; product?: never }

type GrantPending = { family: 'grant_withdraw'; target: number; command: GrantWithdrawCommand }

export const usePendingManagementCommands = defineStore('pending-management-commands', () => {
  const session = useSessionStore()
  const collection = shallowRef<CollectionPending | null>(null)
  const grant = shallowRef<GrantPending | null>(null)
  const has = (action: string) => session.capabilities.actions.includes(action)
  const collectionPermission = (pending: CollectionPending) => pending.envelope.family === 'manual_run' ? 'run_collection' : 'manage_collection'

  watch(() => session.profile?.username, () => {
    collection.value = null
    grant.value = null
  }, { flush: 'sync' })
  watch(() => session.capabilities.actions.slice(), () => {
    if (collection.value && !has(collectionPermission(collection.value))) collection.value = null
    if (!has('manage_grants')) grant.value = null
  }, { flush: 'sync' })

  function beginCollection(pending: CollectionPending) {
    if (!session.authenticated || collection.value || pending.scope !== session.scope || !has(collectionPermission(pending))) return false
    collection.value = pending
    return true
  }

  function finishCollection(pending: CollectionPending) {
    if (collection.value !== pending) return false
    collection.value = null
    return true
  }

  function beginGrant(pending: GrantPending) {
    if (!session.authenticated || grant.value || !has('manage_grants')) return false
    grant.value = pending
    return true
  }

  function finishGrant(pending: GrantPending) {
    if (grant.value !== pending) return false
    grant.value = null
    return true
  }

  return { collection, grant, beginCollection, finishCollection, beginGrant, finishGrant }
})

export function confirmedCommandRejection(cause: unknown): cause is ApiError {
  return cause instanceof ApiError && knownCollectionRejection(cause)
    && cause.code !== 'csrf_failed' && cause.code !== 'request_failed'
}

export function validLookupTimestamp(value: unknown) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

export function validGovernanceLookup(result: GovernanceReceipt, action: string, expectedVersion: number) {
  return Boolean(result && result.action === action && typeof result.state === 'string' && result.state
    && isUuid(result.action_id) && Number.isSafeInteger(result.version) && result.version > expectedVersion
    && validLookupTimestamp(result.recorded_at))
}
