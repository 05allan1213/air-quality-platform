<script setup lang="ts">
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import { isUuid, type GovernanceReceipt } from './collectionLedger'
import { clockText, positiveId } from './operationsContract'
import { confirmedCommandRejection, type GrantWithdrawCommand, usePendingManagementCommands, validGovernanceLookup } from './pendingManagementCommands'

const session = useSessionStore()
const pending = usePendingManagementCommands()
const reasons = {
  terms_ended: '条款终止', retention_withdrawn: '留存许可撤回', rights_review: '权利复核',
} as const
type Grant = { grant: number; product: number; version: number; state: 'registered' | 'withdrawn' }
type Receipt = { action_id: string; version: number; state: 'withdrawn'; recorded_at?: string }
type Disposition = { product: number; version: number; jobs: { id: number; reason: string; error_code: string }[] }
const grantId = ref('')
const productId = ref('')
const reason = ref<keyof typeof reasons | ''>('')
const confirmed = ref(false)
const grantRead = usePrivateRead<Grant>()
const previewRead = usePrivateRead<Disposition>()
const receiptRead = usePrivateRead<GovernanceReceipt>()
const validated = ref<Grant | null>(null)
type OriginalGrant = NonNullable<typeof pending.grant>
const accepted = shallowRef<{ original: OriginalGrant; receipt: Receipt } | null>(null)
const message = ref('')
const receiptMessage = ref('')
const writeInProgress = ref(false)
const withdrawController = shallowRef<AbortController | null>(null)
const previewMessage = ref('')
const validProduct = computed(() => positiveId(productId.value))

function resetGrant() {
  withdrawController.value?.abort()
  withdrawController.value = null
  writeInProgress.value = false
  grantRead.clear()
  validated.value = null
  reason.value = ''
  confirmed.value = false
  message.value = ''
}

function clearPrivate() {
  grantId.value = ''
  productId.value = ''
  resetGrant()
  receiptRead.clear()
  accepted.value = null
  receiptMessage.value = ''
  previewRead.clear()
  previewMessage.value = ''
}

watch([() => session.profile?.username, () => session.capabilities.actions.includes('manage_grants')], clearPrivate, { flush: 'sync' })
watch(grantId, resetGrant, { flush: 'sync' })
watch(productId, () => {
  resetGrant()
  previewRead.clear()
  previewMessage.value = ''
}, { flush: 'sync' })
window.addEventListener('air-quality:clear-reads', clearPrivate)
onScopeDispose(() => {
  window.removeEventListener('air-quality:clear-reads', clearPrivate)
  clearPrivate()
})

function refreshIfRevoked(error: ApiError | null) {
  if (error?.code === 'permission_denied') void session.refresh().catch(() => undefined)
}

async function verify() {
  resetGrant()
  if (!positiveId(grantId.value) || !validProduct.value) { message.value = '请输入交接的有效许可和产品编号。'; return }
  const selected = { grant: Number(grantId.value), product: Number(productId.value) }
  const current = await grantRead.run(signal => api.get<Grant>(`admin/grants/${selected.grant}/actions/`, undefined, signal))
  if (current && (current.product !== selected.product || current.grant !== selected.grant)) {
    grantRead.clear()
    message.value = '许可与交接产品不匹配。请核对交接资料。'
  } else if (current) {
    validated.value = current
  } else if (grantRead.error.value) {
    message.value = '当前无法核验所交接的许可。'
    refreshIfRevoked(grantRead.error.value)
  }
}

function validWithdrawal(result: Receipt, original: OriginalGrant) {
  return Boolean(result && result.state === 'withdrawn' && isUuid(result.action_id)
    && Number.isSafeInteger(result.version) && result.version > original.command.expected_version)
}

function acceptWithdrawal(original: OriginalGrant, receipt: Receipt) {
  accepted.value = { original, receipt }
  if (validated.value?.grant === original.target) {
    validated.value = null
    grantRead.clear()
  }
  previewRead.clear()
  previewMessage.value = ''
}

async function withdraw() {
  const grant = validated.value
  if (!grant || grant.state !== 'registered' || !confirmed.value || !reason.value || pending.grant
    || writeInProgress.value || receiptRead.busy.value) return
  const command: GrantWithdrawCommand = {
    action: 'withdraw', reason_code: reason.value, expected_version: grant.version, request_id: crypto.randomUUID(),
  }
  const original: OriginalGrant = { family: 'grant_withdraw', target: grant.grant, command }
  if (!pending.beginGrant(original)) return
  accepted.value = null
  receiptMessage.value = ''
  const controller = new AbortController()
  const generation = privateStateGeneration()
  const current = () => !controller.signal.aborted && generation === privateStateGeneration() && pending.grant === original
  withdrawController.value = controller
  writeInProgress.value = true
  message.value = ''
  try {
    const result = await api.post<Receipt>(`admin/grants/${grant.grant}/actions/`, command, controller.signal)
    if (!current()) return
    if (!validWithdrawal(result, original)) throw new ApiError('许可撤回回执与原命令不一致。', 502, 'invalid_response')
    if (pending.finishGrant(original)) acceptWithdrawal(original, result)
  } catch (cause) {
    if (!current()) return
    validated.value = null
    grantRead.clear()
    if (confirmedCommandRejection(cause)) {
      pending.finishGrant(original)
      message.value = cause.status === 409 ? '版本或许可状态已变化，请重新核对。' : cause.message
      refreshIfRevoked(cause)
    } else receiptMessage.value = '提交结果待核。请按原命令查询本人回执。'
  } finally {
    if (withdrawController.value === controller) {
      withdrawController.value = null
      writeInProgress.value = false
    }
  }
}

async function lookup() {
  const checked = pending.grant
  const original = checked ?? accepted.value?.original
  if (!original || writeInProgress.value || receiptRead.busy.value) return
  receiptMessage.value = ''
  const result = await receiptRead.run(async signal => {
    const data = await api.post<GovernanceReceipt>('admin/command-receipts/lookup/', original, signal)
    if (data?.state !== 'withdrawn' || !validGovernanceLookup(data, original.command.action, original.command.expected_version)) {
      throw new ApiError('许可撤回回执与原命令不一致。', 502, 'invalid_response')
    }
    return data
  })
  if (checked && pending.grant !== checked) return
  if (result) {
    if (!checked || pending.finishGrant(checked)) acceptWithdrawal(original, { ...result, state: 'withdrawn' })
  } else if (receiptRead.error.value) {
    const error = receiptRead.error.value
    if (error.status === 404 || (error.status === 403 && error.code !== 'csrf_failed')) {
      accepted.value = null
      validated.value = null
      grantRead.clear()
      previewRead.clear()
    }
    receiptMessage.value = error.status === 404 ? '目前无法确认该命令的本人回执。请与运维核对原请求。' : error.message
    refreshIfRevoked(error)
  }
}

async function preview() {
  if (!validProduct.value || writeInProgress.value) return
  previewMessage.value = ''
  const target = Number(productId.value)
  const result = await previewRead.run(signal => api.get<Disposition>(`admin/disposition-products/${target}/`, undefined, signal))
  if (result && result.product !== target) {
    previewRead.clear()
    previewMessage.value = '返回的产品与交接资料不一致。'
  } else if (previewRead.error.value) {
    previewMessage.value = previewRead.error.value.message
    refreshIfRevoked(previewRead.error.value)
  }
}
</script>

<template>
  <section class="grants" aria-labelledby="grants-title">
    <h2 id="grants-title">许可与处置交接</h2>
    <p class="hint">请使用主机已交接的许可与产品编号，人工核对受众、用途、对象、字段和适用时段。实际处置、备份和恢复由主机保管方签认。</p>
    <form class="handoff-form" @submit.prevent="verify">
      <label>交接产品编号<input v-model.trim="productId" inputmode="numeric" maxlength="16" autocomplete="off" :disabled="writeInProgress" required></label>
      <label>交接许可编号<input v-model.trim="grantId" inputmode="numeric" maxlength="16" autocomplete="off" :disabled="writeInProgress" required></label>
      <button class="btn" type="submit" :disabled="grantRead.busy.value || writeInProgress">核对许可</button>
    </form>
    <p v-if="grantRead.busy.value" class="hint" role="status">正在核对交接许可…</p>
    <p v-if="message" class="status status-warning" role="alert">{{ message }}</p>
    <div v-if="validated" class="grant-record">
      <p>产品 {{ validated.product }} · 许可 {{ validated.grant }} · 版本 {{ validated.version }} · {{ validated.state === 'registered' ? '已登记' : '已撤回' }}</p>
      <form v-if="validated.state === 'registered'" class="withdraw-form" @submit.prevent="withdraw">
        <label>撤回原因<select v-model="reason" required><option disabled value="">选择原因</option><option v-for="(label, code) in reasons" :key="code" :value="code">{{ label }}</option></select></label>
        <label class="confirm"><input v-model="confirmed" type="checkbox">我已核对主机交接范围，确认撤回此许可。</label>
        <button class="btn" type="submit" :disabled="!confirmed || !reason || writeInProgress || pending.grant !== null || receiptRead.busy.value">确认撤回许可</button>
      </form>
    </div>
    <p v-if="writeInProgress" class="hint" role="status">正在提交许可撤回…</p>
    <p v-if="receiptMessage" class="status status-warning" role="alert">{{ receiptMessage }}</p>
    <p v-if="pending.grant && !writeInProgress" class="hint" role="status">原许可 {{ pending.grant.target }} 的撤回结果待核。<br>原请求 <code>{{ pending.grant.command.request_id }}</code></p>
    <p v-if="accepted" class="status" role="status">许可已撤回 · 许可 {{ accepted.original.target }} · 版本 {{ accepted.receipt.version }} · 动作 {{ accepted.receipt.action_id }}<span v-if="accepted.receipt.recorded_at"> · {{ clockText(accepted.receipt.recorded_at) }}</span></p>
    <button v-if="(pending.grant || accepted) && !writeInProgress" class="btn" type="button" :disabled="receiptRead.busy.value" @click="lookup">查询本人撤回回执</button>

    <div class="disposition">
      <h3>处置预览</h3>
      <p class="hint">查看交接产品当前到期的留存处置待办。</p>
      <button class="btn" type="button" :disabled="!validProduct || previewRead.busy.value || writeInProgress" @click="preview">读取处置待办</button>
      <p v-if="previewMessage" class="status status-warning" role="alert">{{ previewMessage }}</p>
      <p v-else-if="previewRead.busy.value" class="hint" role="status">正在读取处置待办…</p>
      <template v-else-if="previewRead.data.value">
        <p class="hint" role="status">产品 {{ previewRead.data.value.product }} · 许可版本 {{ previewRead.data.value.version }} · {{ previewRead.data.value.jobs.length ? `${previewRead.data.value.jobs.length} 项待办` : '当前无到期待办' }}</p>
        <ul v-if="previewRead.data.value.jobs.length" class="jobs">
          <li v-for="job in previewRead.data.value.jobs" :key="job.id">待办 {{ job.id }} · {{ job.reason }}<span v-if="job.error_code"> · {{ job.error_code }}</span></li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.grants { min-width: 0; padding: 20px 0; border-bottom: 1px solid var(--aq-border); }
h2 { margin-bottom: 0; }
.hint { margin: 4px 0 12px; color: var(--aq-secondary); font-size: .88rem; }
.handoff-form, .withdraw-form { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; }
label { display: grid; min-width: 0; gap: 4px; color: var(--aq-secondary); font-size: .81rem; font-weight: 620; }
input:not([type='checkbox']), select { width: 100%; min-width: 0; min-height: 40px; padding: 6px 8px; border: 1px solid var(--aq-control-border); border-radius: 6px; background: var(--aq-content); color: var(--aq-text); }
.confirm { display: flex; align-items: center; min-height: 40px; gap: 7px; }
.confirm input { flex: none; }
.grant-record, .disposition { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--aq-border); }
.grant-record p, .disposition h3 { margin: 0 0 8px; }
.status, .jobs { overflow-wrap: anywhere; }
@media (max-width: 520px) {
  .handoff-form > label, .withdraw-form > label { flex-basis: 100%; }
}
</style>
