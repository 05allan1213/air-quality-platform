<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import CollectionRunDetail from './CollectionRunDetail.vue'
import CollectionRunHistoryImport from './CollectionRunHistoryImport.vue'
import {
  addUtcDays, buildJobControlCommand, buildRedeliveryCommand, buildRunCommand, errorText, isUuid, label,
  ledgerPath, nextCursor, nextUtcHour, todayUtc, validDateSpan, validSubmissionReceipt, validTarget,
  type CollectionWindowProjection, type CursorPage, type DirectoryEntry, type ExecutionEntry, type GovernanceReceipt,
  type JobAction, type JobDiagnostic, type JobRow, type Purpose, type QualityContext, type RunCommand,
  type RunEntry, type RunReason, type Scope, type SubmissionReceipt, type WindowRow,
} from './collectionLedger'
import { confirmedCommandRejection, usePendingManagementCommands, validGovernanceLookup, validLookupTimestamp } from './pendingManagementCommands'

const props = defineProps<{ productId?: number; scope?: Scope; initialPurpose?: Purpose }>()
const emit = defineEmits<{
  'job-selected': [job: number]
  'window-selected': [window: number]
  'run-accepted': [receipt: SubmissionReceipt]
  'control-accepted': [event: { action: JobAction | 'redeliver'; id: number | string }]
  'quality-open': [context: QualityContext]
  'history-submitted': [attemptId: string]
}>()
const session = useSessionStore()
const pending = usePendingManagementCommands()
const router = useRouter()
const domain = computed(() => props.scope ?? session.scope)
const granted = (action: string) => session.capabilities.actions.includes(action)
const canReview = computed(() => granted('review_observation'))
const canManage = computed(() => granted('manage_collection'))
const canExecute = computed(() => granted('run_collection'))
const canImport = computed(() => granted('import_history'))
const synthetic = computed(() => domain.value === 'synthetic' && session.scope === 'synthetic')
const canSubmit = computed(() => canExecute.value && synthetic.value)
const canReadJobs = computed(() => canManage.value)
const defaultPurpose = () => props.initialPurpose ?? (canReview.value || canManage.value ? 'ledger' : canExecute.value ? 'execute' : canImport.value && !domain.value ? 'history' : 'ledger')
const purpose = ref<Purpose | 'history'>(defaultPurpose())
const start = ref(addUtcDays(todayUtc(), -6))
const end = ref(addUtcDays(todayUtc(), 1))
const objectCode = ref('')
const indicator = ref('')
const windowFilter = ref<{ object_code?: string; indicator?: string }>({})
const target = ref(nextUtcHour())
const reason = ref<RunReason>('manual')
const product = ref<DirectoryEntry | null>(null)
const job = ref<JobRow | null>(null)
const selectedWindow = ref<WindowRow | null>(null)
const selectedRun = ref<string | null>(null)
const runContext = ref<{ window?: number; product: number } | null>(null)
const cellOffset = ref<number | undefined>()
const runOffset = ref<number | undefined>()
const execution = ref<ExecutionEntry | null>(null)
const receipt = ref<SubmissionReceipt | null>(null)
type PendingCollection = NonNullable<typeof pending.collection>
type PendingRun = Extract<PendingCollection, { product: number }>
type PendingControl = Exclude<PendingCollection, PendingRun>
const acceptedRun = ref<{ command: RunCommand; product: number } | null>(null)
const localPending = computed(() => pending.collection?.scope === domain.value && domain.value === session.scope ? pending.collection : null)
const pendingRun = computed((): PendingRun | null => localPending.value?.product !== undefined ? localPending.value : null)
const pendingControl = computed((): PendingControl | null => localPending.value && localPending.value.product === undefined ? localPending.value : null)
const originalRun = computed(() => pendingRun.value?.envelope.command ?? acceptedRun.value?.command)
const controlReceipt = ref<GovernanceReceipt | null>(null)
const awaitingOutcome = computed(() => pending.collection !== null)
const confirm = ref<{ kind: 'execute' } | { kind: 'job'; action: JobAction } | { kind: 'redeliver'; run: RunEntry } | null>(null)
const writeError = ref('')
const notice = ref('')
const busyWrite = ref(false)
let writeController: AbortController | null = null

const catalogRead = usePrivateRead<CursorPage<DirectoryEntry>>()
const executeRead = usePrivateRead<CursorPage<ExecutionEntry>>()
const jobsRead = usePrivateRead<CursorPage<JobRow>>()
const controlRead = usePrivateRead<JobDiagnostic>()
const windowsRead = usePrivateRead<CursorPage<WindowRow>>()
const viewRead = usePrivateRead<CollectionWindowProjection>()
const { data: catalog, busy: catalogBusy, error: catalogError } = catalogRead
const { data: executions, busy: executeBusy, error: executeError } = executeRead
const { data: jobs, busy: jobsBusy, error: jobsError } = jobsRead
const { data: diagnostic, busy: controlBusy, error: controlError } = controlRead
const { data: windows, busy: windowsBusy, error: windowsError } = windowsRead
const { data: projection, busy: viewBusy, error: viewError } = viewRead
const targetIsValid = computed(() => validTarget(target.value))
const actionLabels: Record<JobAction, string> = { pause: '暂停 Job', resume: '恢复 Job', resume_source: '恢复来源', resume_product: '恢复产品' }
const actionTargets = computed(() => {
  const targets = diagnostic.value?.targets
  if (!targets) return []
  const actions: JobAction[] = []
  if (targets.job.state === 'active') actions.push('pause')
  if (synthetic.value) {
    if (targets.job.state === 'paused') actions.push('resume')
    if (targets.source.state === 'paused') actions.push('resume_source')
    if (targets.product.state === 'paused') actions.push('resume_product')
  }
  return actions.map(action => ({ action, target: targets[action === 'resume_source' ? 'source' : action === 'resume_product' ? 'product' : 'job'] }))
})
const confirmText = computed(() => {
  const action = confirm.value
  if (action?.kind === 'execute' && execution.value) return `提交 F 合成 Job ${execution.value.job}，产品 ${execution.value.product}，目标 ${execution.value.target}，理由：${label(execution.value.reason)}？`
  if (action?.kind === 'job') {
    const entry = actionTargets.value.find(item => item.action === action.action)
    if (entry) return `${actionLabels[entry.action]} ${entry.target.id}（通过 Job ${diagnostic.value?.job}，版本 v${entry.target.control_version}）？`
  }
  if (action?.kind === 'redeliver' && projection.value?.target) return `重新投递原 Run ${action.run.id}（版本 v${projection.value.target.control_version}），不创建新 Run？`
  return ''
})

function clearView() {
  viewRead.clear()
  selectedWindow.value = null
  selectedRun.value = null
  runContext.value = null
  cellOffset.value = undefined
  runOffset.value = undefined
}

function clearLedger() {
  catalogRead.clear()
  jobsRead.clear()
  controlRead.clear()
  windowsRead.clear()
  objectCode.value = ''
  indicator.value = ''
  windowFilter.value = {}
  product.value = null
  job.value = null
  clearView()
}

function reset() {
  writeController?.abort()
  writeController = null
  busyWrite.value = false
  confirm.value = null
  writeError.value = ''
  notice.value = ''
  executeRead.clear()
  execution.value = null
  receipt.value = null
  acceptedRun.value = null
  controlReceipt.value = null
  clearLedger()
}

window.addEventListener('air-quality:clear-reads', reset)
onScopeDispose(() => { window.removeEventListener('air-quality:clear-reads', reset); reset() })
watch([domain, () => props.productId, () => session.profile?.username, canReview, canManage, canExecute, canImport], () => {
  reset()
  purpose.value = defaultPurpose()
}, { flush: 'sync' })
watch([start, end], () => { clearLedger(); confirm.value = null }, { flush: 'sync' })
watch([target, reason], () => { executeRead.clear(); execution.value = null; confirm.value = null }, { flush: 'sync' })
watch(purpose, () => { confirm.value = null; writeError.value = ''; notice.value = '' })

function readCatalog(cursor?: string) {
  if (!canReview.value || !validDateSpan(start.value, end.value) || busyWrite.value) return
  clearLedger()
  void catalogRead.run(signal => api.get<CursorPage<DirectoryEntry>>('admin/catalog/products/', {
    purpose: 'ledger', scope: domain.value || undefined, product: props.productId,
    start: start.value, end: end.value, page_size: 25, cursor,
  }, signal))
}

function selectProduct(entry: DirectoryEntry) {
  if (!canReview.value || busyWrite.value || !catalog.value?.results.includes(entry)) return
  product.value = entry
  objectCode.value = ''
  indicator.value = ''
  windowFilter.value = {}
  jobsRead.clear()
  controlRead.clear()
  job.value = null
  confirm.value = null
  readWindows()
}

function readJobs(cursor?: string) {
  if (!canReadJobs.value || busyWrite.value) return
  job.value = null
  confirm.value = null
  controlRead.clear()
  if (product.value) readWindows()
  void jobsRead.run(signal => api.get<CursorPage<JobRow>>('admin/collection-jobs/', {
    product: product.value?.product ?? props.productId, scope: domain.value || undefined, page_size: 25, cursor,
  }, signal))
}

function readControl(id: number) {
  return controlRead.run(async signal => {
    const result = await api.get<JobDiagnostic>(`admin/collection-jobs/${id}/`, undefined, signal)
    if (result.job !== id || result.targets.job.id !== id || (job.value && result.targets.product.id !== job.value.product)) throw new ApiError('Job 投影与选择不一致。', 502, 'invalid_response')
    return result
  })
}

function selectJob(entry: JobRow) {
  if (!canReadJobs.value || busyWrite.value || entry.synthetic !== (domain.value === 'synthetic') || !jobs.value?.results.includes(entry)) return
  job.value = entry
  confirm.value = null
  emit('job-selected', entry.id)
  void readControl(entry.id)
  if (product.value && canReview.value) readWindows()
}

function applyWindowFilter() {
  windowFilter.value = { object_code: objectCode.value || undefined, indicator: indicator.value || undefined }
  readWindows()
}

function readWindows(cursor?: string) {
  const entry = product.value
  if (!canReview.value || !entry || busyWrite.value) return
  clearView()
  confirm.value = null
  const path = ledgerPath(entry.links.windows, 'admin/collection-windows/')
  if (!path) return
  void windowsRead.run(signal => api.get<CursorPage<WindowRow>>(path, { ...windowFilter.value, job: job.value?.id, page_size: 25, cursor }, signal))
}

function selectWindow(entry: WindowRow) {
  if (!canReview.value || busyWrite.value || entry.product !== product.value?.product || entry.synthetic !== (domain.value === 'synthetic') || !windows.value?.results.includes(entry)) return
  clearView()
  selectedWindow.value = entry
  emit('window-selected', entry.id)
  void readView()
}

function selectRun(id: string) {
  if (!canReview.value || busyWrite.value || !isUuid(id) || !projection.value?.runs?.some(run => run.id === id)) return
  confirm.value = null
  selectedRun.value = id
  cellOffset.value = undefined
  void readView()
}

function readView() {
  const row = selectedWindow.value
  const runId = selectedRun.value
  const context = runContext.value
  const windowId = row?.id ?? context?.window
  const productId = row?.product ?? context?.product
  const isSynthetic = domain.value === 'synthetic'
  if (!canReview.value || (!runId && !row)) return
  const path = runId ? `admin/collection-runs/${runId}/` : ledgerPath(row?.links.detail, `admin/collection-windows/${windowId}/`)
  if (!path) return
  return viewRead.run(async signal => {
    const result = await api.get<CollectionWindowProjection>(path, {
      after: cellOffset.value, run_after: runId ? undefined : runOffset.value,
    }, signal)
    if ((windowId !== undefined && windowId !== result.window_id)
      || (result.coverage !== null && (result.product !== productId || result.synthetic !== isSynthetic))
      || (runId && result.runs?.some(run => run.id !== runId))) throw new ApiError('读到的投影与当前选择不一致。', 502, 'invalid_response')
    return result
  })
}

function readExecutions(cursor?: string) {
  if (!canSubmit.value || !targetIsValid.value || busyWrite.value) return
  execution.value = null
  confirm.value = null
  void executeRead.run(signal => api.get<CursorPage<ExecutionEntry>>('admin/catalog/products/', {
    purpose: 'execute', scope: domain.value, product: props.productId,
    target: target.value, reason: reason.value, page_size: 25, cursor,
  }, signal))
}

function openAcceptedRun() {
  if (!canReview.value || !receipt.value || !acceptedRun.value || !isUuid(receipt.value.run_id)) return
  clearView()
  runContext.value = { window: receipt.value.window_id, product: acceptedRun.value.product }
  selectedRun.value = receipt.value.run_id
  purpose.value = 'ledger'
  void readView()
}

function qualityOpen(context: Omit<QualityContext, 'product'>) {
  if (!canReview.value) return
  const productId = projection.value?.product ?? selectedWindow.value?.product
  if (productId == null) return
  emit('quality-open', { product: productId, ...context })
  void router.push({ name: 'manage-quality', query: {
    ...scopeQuery(domain.value), product: String(productId), window: String(context.window), run: context.run,
  } })
}

async function work(task: (signal: AbortSignal, current: () => boolean) => Promise<void>, knownFailure?: (cause: unknown) => boolean) {
  if (busyWrite.value) return
  confirm.value = null
  writeError.value = ''
  notice.value = ''
  busyWrite.value = true
  const controller = new AbortController()
  const generation = privateStateGeneration()
  writeController = controller
  const current = () => !controller.signal.aborted && generation === privateStateGeneration()
  try {
    await task(controller.signal, current)
  } catch (cause) {
    if (current() && !knownFailure?.(cause)) writeError.value = errorText(cause, '请求未完成；请先核对原操作。')
  } finally {
    if (writeController === controller) { writeController = null; busyWrite.value = false }
  }
}

function acceptRun(result: SubmissionReceipt, original: PendingRun, wasPending: boolean) {
  if (!validSubmissionReceipt(result)) throw new ApiError('运行回执不可用，请按原请求查账。', 502, 'invalid_response')
  if (wasPending && !pending.finishCollection(original)) return
  acceptedRun.value = { command: original.envelope.command, product: original.product }
  receipt.value = result
  emit('run-accepted', result)
}

function lookupDenied(cause: unknown, clearProjection: () => void) {
  if (!(cause instanceof ApiError) || (cause.status !== 404 && (cause.status !== 403 || cause.code === 'csrf_failed'))) return
  clearProjection()
  if (cause.code === 'permission_denied') void session.refresh().catch(() => undefined)
}

function checkOriginal() {
  const checked = pendingRun.value
  const accepted = acceptedRun.value
  const original: PendingRun | null = checked ?? (accepted ? {
    scope: domain.value, product: accepted.product,
    envelope: { family: 'manual_run', target: accepted.command.job, command: accepted.command },
  } : null)
  if (!original || !canSubmit.value) return
  void work(async (signal, current) => {
    const result = await api.post<SubmissionReceipt & Pick<GovernanceReceipt, 'state' | 'action' | 'recorded_at'>>('admin/command-receipts/lookup/', original.envelope, signal)
    if (!current()) return
    if (result?.state !== 'submitted' || result.action !== original.envelope.command.reason
      || !validLookupTimestamp(result.recorded_at) || typeof result.dispatch !== 'string' || typeof result.status !== 'string') {
      throw new ApiError('运行回执与原命令不一致。', 502, 'invalid_response')
    }
    acceptRun(result, original, checked !== null)
  }, cause => {
    lookupDenied(cause, () => { receipt.value = null; acceptedRun.value = null; clearView() })
    writeError.value = '暂未查回原命令的本人回执。请与运维核对原请求。'
    return true
  })
}

function rereadControl(original: PendingControl['envelope']) {
  if (original.family === 'job_control' && job.value?.id === original.target) return readControl(original.target)
  if (original.family === 'redeliver' && selectedRun.value === original.target) return readView()
}

function checkControl() {
  const checked = pendingControl.value
  if (!checked || !canManage.value) return
  const original = checked.envelope
  void work(async (signal, current) => {
    const result = await api.post<GovernanceReceipt & Partial<SubmissionReceipt>>('admin/command-receipts/lookup/', original, signal)
    if (!current() || pending.collection !== checked) return
    if (!validGovernanceLookup(result, original.command.action, original.command.expected_version)
      || (original.family === 'redeliver' && (!validSubmissionReceipt(result as SubmissionReceipt, original.target)
        || typeof result.dispatch !== 'string' || typeof result.status !== 'string'))) {
      throw new ApiError('控制回执与原命令不一致。', 502, 'invalid_response')
    }
    pending.finishCollection(checked)
    controlReceipt.value = result
    notice.value = '已查回原命令的本人回执。'
    await rereadControl(original)
  }, cause => {
    lookupDenied(cause, () => {
      controlReceipt.value = null
      controlRead.clear()
      clearView()
    })
    writeError.value = '暂未查回原控制命令的本人回执。请与运维核对原请求。'
    return true
  })
}

function handleControlFailure(cause: unknown, original: PendingControl) {
  if (!confirmedCommandRejection(cause)) {
    writeError.value = '控制结果待核。请查本人回执。'
    return true
  }
  if (!pending.finishCollection(original)) return true
  if ([403, 404].includes(cause.status)) {
    if (original.envelope.family === 'job_control') { job.value = null; jobsRead.clear() }
    else clearView()
    if (cause.code === 'permission_denied') void session.refresh().catch(() => undefined)
  }
  if (cause.status === 409) void rereadControl(original.envelope)
  return false
}

function submit() {
  const candidate = confirm.value
  if (!candidate || !confirmText.value || busyWrite.value || awaitingOutcome.value || domain.value !== session.scope) return
  if (candidate.kind === 'execute') {
    const selected = execution.value
    if (!canSubmit.value || !selected || !executions.value?.results.includes(selected)) return
    const command = buildRunCommand(selected.job, selected.target, selected.reason)
    const original: PendingRun = {
      scope: domain.value, product: selected.product,
      envelope: { family: 'manual_run', target: command.job, command },
    }
    if (!pending.beginCollection(original)) return
    execution.value = null
    acceptedRun.value = null
    receipt.value = null
    void work(async (signal, current) => {
      const result = await api.post<SubmissionReceipt>('admin/collection-runs/', command, signal)
      if (current()) acceptRun(result, original, true)
    }, cause => {
      if (confirmedCommandRejection(cause)) {
        if (!pending.finishCollection(original)) return true
        executeRead.clear()
        if (cause.code === 'permission_denied') void session.refresh().catch(() => undefined)
        return false
      }
      writeError.value = '采集受理结果待核。请查本人回执。'
      return true
    })
  } else if (candidate.kind === 'job') {
    const entry = actionTargets.value.find(item => item.action === candidate.action)
    const id = diagnostic.value?.job
    if (!canManage.value || !entry || id == null) return
    const command = buildJobControlCommand(entry.target, entry.action)
    const original: PendingControl = { scope: domain.value, envelope: { family: 'job_control', target: id, command } }
    if (!pending.beginCollection(original)) return
    controlReceipt.value = null
    controlRead.clear()
    void work(async (signal, current) => {
      const result = await api.post<{ job: number; enabled: boolean; reason: string }>(`admin/collection-jobs/${id}/`, command, signal)
      if (!current()) return
      if (result?.job !== id || typeof result.enabled !== 'boolean' || typeof result.reason !== 'string') {
        throw new ApiError('Job 回执不可用。', 502, 'invalid_response')
      }
      if (!pending.finishCollection(original)) return
      notice.value = `${actionLabels[entry.action]}已受理。`
      emit('control-accepted', { action: entry.action, id: entry.target.id })
      await rereadControl(original.envelope)
    }, cause => handleControlFailure(cause, original))
  } else {
    const run = candidate.run
    const version = projection.value?.target
    if (!canManage.value || !synthetic.value || version?.id !== run.id || selectedRun.value !== run.id) return
    const command = buildRedeliveryCommand(version)
    const original: PendingControl = { scope: domain.value, envelope: { family: 'redeliver', target: run.id, command } }
    if (!pending.beginCollection(original)) return
    controlReceipt.value = null
    viewRead.clear()
    void work(async (signal, current) => {
      const result = await api.post<SubmissionReceipt>(`admin/collection-runs/${run.id}/`, command, signal)
      if (!current()) return
      if (!validSubmissionReceipt(result, run.id)) throw new ApiError('重投回执不可用。', 502, 'invalid_response')
      if (!pending.finishCollection(original)) return
      notice.value = '原 Run 重投已受理，执行阶段和结果请查看原 Run。'
      emit('control-accepted', { action: 'redeliver', id: run.id })
      await rereadControl(original.envelope)
    }, cause => handleControlFailure(cause, original))
  }
}

</script>

<template>
  <section v-if="canReview || canManage || canExecute || canImport" class="panel ledger-panel" aria-labelledby="collection-ledger-title" :aria-busy="busyWrite">
    <header class="section-head ledger-head">
      <div><h2 id="collection-ledger-title">采集账本与执行</h2><p>按获权用途选择业务窗口或执行目标，查看状态、证据和回执。</p></div>
      <span class="ledger-domain">{{ domain ? 'F 合成域' : '正式域' }}</span>
    </header>
    <div class="ledger-mode" role="group" aria-label="采集工作区用途">
      <button v-if="canReview || canManage" class="btn" :class="{ primary: purpose === 'ledger' }" type="button" :aria-pressed="purpose === 'ledger'" :disabled="busyWrite" @click="purpose = 'ledger'">{{ canReview ? '查看账本' : 'Job 控制' }}</button>
      <button v-if="canExecute" class="btn" :class="{ primary: purpose === 'execute' }" type="button" :aria-pressed="purpose === 'execute'" :disabled="busyWrite" @click="purpose = 'execute'">执行采集</button>
      <button v-if="(canImport || canReview) && !domain" class="btn" :class="{ primary: purpose === 'history' }" type="button" :aria-pressed="purpose === 'history'" :disabled="busyWrite" @click="purpose = 'history'">历史导入</button>
    </div>
    <p v-if="writeError" class="status status-error" role="alert">{{ writeError }}</p>
    <p v-if="notice" class="status status-success" role="status">{{ notice }}</p>

    <p v-if="awaitingOutcome && !localPending" class="status status-warning" role="status">另一数据域仍有原采集命令待核，请切回提交时的数据域查询本人回执。</p>
    <section v-if="originalRun && canSubmit" class="ledger-block" aria-label="本人执行回执">
      <h3>{{ receipt ? '采集受理回执' : '原请求待核' }}</h3>
      <p v-if="receipt" class="status" :class="receipt.dispatch === 'unconfirmed' ? 'status-warning' : 'status-success'" role="status">Run {{ receipt.run_id }} · {{ receipt.dispatch ? label(receipt.dispatch) : '已受理' }}<template v-if="receipt.status"> · {{ label(receipt.status) }}</template>。受理回执不证明采集完成。</p>
      <p class="ledger-muted">原请求 <code>{{ originalRun.request_id }}</code></p>
      <div class="ledger-actions"><button type="button" class="btn" :disabled="busyWrite" @click="checkOriginal">查本人执行回执</button><button v-if="receipt && canReview" type="button" class="btn" :disabled="busyWrite" @click="openAcceptedRun">查看原 Run</button></div>
    </section>
    <section v-if="pendingControl" class="ledger-block" aria-label="原控制命令">
      <h3>原控制命令待核</h3>
      <p class="ledger-muted">原请求 <code>{{ pendingControl.envelope.command.request_id }}</code></p>
      <div class="ledger-actions"><button class="btn" type="button" :disabled="busyWrite" @click="checkControl">查本人控制回执</button></div>
    </section>
    <p v-if="controlReceipt" class="ledger-muted" role="status">回执 {{ controlReceipt.action_id }} · 版本 {{ controlReceipt.version }}</p>

    <template v-if="purpose === 'ledger'">
      <form v-if="canReview" class="ledger-filter" aria-label="获权账本目录" @submit.prevent="readCatalog()">
        <label class="field">来源日期起点<input v-model="start" type="date" required :disabled="busyWrite" /></label>
        <label class="field">来源日期终点（不含）<input v-model="end" type="date" required :disabled="busyWrite" /></label>
        <button class="btn" type="submit" :disabled="catalogBusy || busyWrite || !validDateSpan(start, end)">读取账本目录</button>
        <p class="form-hint">按来源本地日查询，最多 30 天，终点不含在窗口内。</p>
      </form>
      <p v-if="catalogBusy" role="status">正在读取获权产品…</p>
      <p v-if="catalogError" class="status status-error" role="alert">{{ errorText(catalogError, '账本目录读取失败。') }}</p>
      <div v-if="catalog?.results.length" class="ledger-list" aria-label="获权账本产品">
        <button v-for="entry in catalog.results" :key="entry.product" type="button" class="btn" :disabled="busyWrite" :aria-pressed="product?.product === entry.product" @click="selectProduct(entry)">产品 {{ entry.product }} · {{ entry.shape }} · {{ entry.source }}</button>
        <button v-if="nextCursor(catalog)" class="btn" type="button" :disabled="busyWrite" @click="readCatalog(nextCursor(catalog) ?? undefined)">下一页产品</button>
      </div>
      <p v-else-if="catalog" class="ledger-muted">此日期条件没有获权产品。</p>
      <section v-if="canReadJobs" class="ledger-block" aria-labelledby="collection-job-title">
        <div class="ledger-line"><h3 id="collection-job-title">Job 控制</h3><button class="btn" type="button" :disabled="jobsBusy || busyWrite" @click="readJobs()">读取获权 Job</button></div>
        <p v-if="jobsError" class="status status-error" role="alert">{{ errorText(jobsError, 'Job 列表读取失败。') }}</p>
        <p v-if="jobs && !jobs.results.length" class="ledger-muted">当前范围没有获权 Job。</p>
        <div v-if="jobs?.results.length" class="ledger-list" aria-label="获权 Job">
          <button v-for="entry in jobs.results" :key="entry.id" type="button" class="btn" :disabled="busyWrite" :aria-pressed="job?.id === entry.id" @click="selectJob(entry)">Job {{ entry.id }} · 产品 {{ entry.product }} · {{ label(entry.state) }}</button>
          <button v-if="nextCursor(jobs)" class="btn" type="button" :disabled="busyWrite" @click="readJobs(nextCursor(jobs) ?? undefined)">下一页 Job</button>
        </div>
        <p v-if="controlBusy" role="status">正在读取控制版本…</p>
        <p v-if="controlError" class="status status-error" role="alert">{{ errorText(controlError, '无法读取控制版本。') }}</p>
        <div v-if="job" class="ledger-actions"><button type="button" class="btn" :disabled="controlBusy || busyWrite" @click="confirm = null; readControl(job.id)">重新读取控制状态</button></div>
        <template v-if="diagnostic">
          <dl class="ledger-targets"><div v-for="entry in diagnostic.targets" :key="entry.type"><dt>{{ { job: 'Job', source: '来源', product: '产品', run: 'Run' }[entry.type] }} {{ entry.id }}</dt><dd>{{ label(entry.state) }} · v{{ entry.control_version }}<small v-if="entry.reason">{{ label(entry.reason) }}</small></dd></div></dl>
          <div class="ledger-actions"><button v-for="entry in actionTargets" :key="entry.action" class="btn" type="button" :disabled="busyWrite || awaitingOutcome" @click="confirm = { kind: 'job', action: entry.action }">{{ actionLabels[entry.action] }}</button></div>
        </template>
      </section>
      <section v-if="product" class="ledger-block" aria-labelledby="ledger-window-list-title">
        <div class="ledger-line"><h3 id="ledger-window-list-title">获权业务窗口</h3><button class="btn" type="button" :disabled="windowsBusy || busyWrite" @click="readWindows()">重新读取窗口</button></div>
        <form class="ledger-filter" aria-label="获权窗口筛选" @submit.prevent="applyWindowFilter">
          <label class="field">来源对象代码<input v-model.trim="objectCode" maxlength="100" :disabled="busyWrite" /></label>
          <label class="field">指标代码<input v-model.trim="indicator" maxlength="16" :disabled="busyWrite" /></label>
          <button class="btn" type="submit" :disabled="windowsBusy || busyWrite">筛选窗口</button>
        </form>
        <p v-if="windowsBusy" role="status">正在读取业务窗口…</p>
        <p v-if="windowsError" class="status status-error" role="alert">{{ errorText(windowsError, '窗口列表读取失败。') }}</p>
        <p v-else-if="windows && !windows.results.length" class="ledger-muted">当前条件没有获权窗口。</p>
        <div v-if="windows?.results.length" class="ledger-scroll" tabindex="0" role="region" aria-label="获权业务窗口表">
          <table><caption class="sr-only">当前产品的获权业务窗口</caption><thead><tr><th scope="col">窗口</th><th scope="col">业务时段（右端不含）</th><th scope="col">授权范围</th><th scope="col">窗口结果</th><th scope="col">操作</th></tr></thead><tbody>
            <tr v-for="entry in windows.results" :key="entry.id"><th scope="row">{{ entry.id }}</th><td>{{ entry.target_start }} 至 {{ entry.target_end }}</td><td>{{ entry.full_scope ? '完整授权' : '部分授权' }}</td><td>{{ entry.full_scope ? label(entry.result) : '未披露' }}</td><td><button class="btn" type="button" :aria-pressed="selectedWindow?.id === entry.id" :disabled="busyWrite" @click="selectWindow(entry)">查看窗口 {{ entry.id }}</button></td></tr>
          </tbody></table>
        </div>
        <button v-if="nextCursor(windows)" class="btn" type="button" :disabled="windowsBusy || busyWrite" @click="readWindows(nextCursor(windows) ?? undefined)">下一页窗口</button>
      </section>
      <div v-if="canReview && (selectedWindow || selectedRun)" class="ledger-block">
        <div class="ledger-actions">
          <button v-if="selectedWindow && selectedRun" class="btn" type="button" :disabled="busyWrite" @click="selectedRun = null; cellOffset = undefined; confirm = null; readView()">返回窗口全部 Run</button>
          <button v-if="projection?.coverage && projection.product" class="btn" type="button" :disabled="busyWrite" @click="qualityOpen({ window: projection.window_id, run: selectedRun ?? undefined })">{{ selectedRun ? '查看 Run 质量事件' : '查看窗口质量事件' }}</button>
        </div>
        <p v-if="viewError" class="status status-error" role="alert">{{ errorText(viewError, '证据投影读取失败。') }}</p>
        <CollectionRunDetail :projection="projection" :selected-run-id="selectedRun" :can-manage="canManage && synthetic && !busyWrite && !awaitingOutcome" :loading="viewBusy"
          @select-run="selectRun" @redeliver="confirm = { kind: 'redeliver', run: $event }" @quality-open="qualityOpen"
          @more-cells="cellOffset = $event; confirm = null; readView()" @more-runs="runOffset = $event; confirm = null; readView()"
          @refresh="confirm = null; readView()" />
      </div>
    </template>

    <template v-else-if="purpose === 'execute' && canExecute">
      <p v-if="!canSubmit" class="status status-warning">正式域不开放网页采集执行。本页不会向真实来源提交采集请求。</p>
      <template v-else>
        <form class="ledger-filter" aria-label="获权执行目标" @submit.prevent="readExecutions()">
          <label class="field">目标整点（含时区）<input v-model.trim="target" type="text" required spellcheck="false" :disabled="busyWrite" placeholder="2024-02-02T00:00:00Z" /></label>
          <label class="field">执行理由<select v-model="reason" :disabled="busyWrite"><option value="manual">手动运行</option><option v-if="canReview" value="review">复核运行</option><option value="backfill">有界补采</option></select></label>
          <button class="btn" type="submit" :disabled="executeBusy || busyWrite || !targetIsValid">读取执行目标</button>
          <p class="form-hint">只列出当前整点与理由下通过服务端校验的合成目标。列入目录不表示已受理。</p>
        </form>
        <p v-if="executeBusy" role="status">正在读取获权执行目标…</p>
        <p v-if="executeError" class="status status-error" role="alert">{{ errorText(executeError, '执行目录读取失败。') }}</p>
        <p v-if="executions && !executions.results.length" class="ledger-muted">当前条件没有可执行目标。</p>
        <div v-if="executions?.results.length" class="ledger-list" aria-label="获权执行 Job">
          <button v-for="entry in executions.results" :key="`${entry.product}:${entry.job}`" class="btn" type="button" :disabled="busyWrite" :aria-pressed="execution?.job === entry.job" @click="execution = entry; confirm = null; emit('job-selected', entry.job)">Job {{ entry.job }} · 产品 {{ entry.product }}</button>
          <button v-if="nextCursor(executions)" class="btn" type="button" :disabled="executeBusy || busyWrite" @click="readExecutions(nextCursor(executions) ?? undefined)">下一页执行目标</button>
        </div>
        <div class="ledger-actions"><button v-if="execution" class="btn primary" type="button" :disabled="busyWrite || awaitingOutcome" @click="confirm = { kind: 'execute' }">提交合成采集</button></div>
      </template>
    </template>
    <CollectionRunHistoryImport v-if="!domain && (canImport || canReview)" v-show="purpose === 'history'" :can-import="canImport" @submitted="emit('history-submitted', $event)" />
    <div v-if="confirmText" class="ledger-confirm" role="group" aria-label="确认采集操作">
      <p>{{ confirmText }}</p><button class="btn primary" type="button" :disabled="busyWrite || awaitingOutcome" @click="submit">确认提交</button><button class="btn" type="button" @click="confirm = null">取消</button>
    </div>
  </section>
</template>

<style scoped>
.ledger-panel { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; min-width: 0; padding: clamp(14px, 2vw, 20px); overflow-wrap: anywhere; }
.ledger-head { align-items: center; }
.ledger-head h2, .ledger-line h3 { margin: 0; }
.ledger-head p { margin-top: 4px; font-size: 13px; }
.ledger-domain { flex: none; color: var(--aq-secondary); font-size: 13px; }
.ledger-mode, .ledger-actions, .ledger-list { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ledger-list .btn { max-width: 100%; text-align: start; }
.ledger-list .btn[aria-pressed='true'], .ledger-scroll .btn[aria-pressed='true'] { border-color: var(--aq-brand); background: var(--aq-content-strong); }
.ledger-filter { display: grid; grid-template-columns: repeat(2, minmax(170px, 1fr)) auto; align-items: end; gap: 10px; }
.ledger-filter select { width: 100%; min-height: 42px; padding: 8px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: var(--aq-content); color: var(--aq-text); }
.ledger-filter .btn { min-height: 42px; }
.ledger-filter .field, .ledger-filter input { min-width: 0; }
.ledger-line { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.ledger-block { display: grid; gap: 10px; min-width: 0; padding-top: 12px; border-top: 1px solid var(--aq-border); }
.ledger-block h3 { margin: 0; font-size: 16px; }
.ledger-muted { margin: 0; color: var(--aq-muted); font-size: 13px; overflow-wrap: anywhere; }
.ledger-targets { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0; }
.ledger-targets div { padding: 8px 10px; background: var(--aq-content); }
.ledger-targets dt { font-size: 12px; color: var(--aq-muted); }
.ledger-targets dd { margin: 2px 0 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.ledger-targets small { display: block; color: var(--aq-muted); }
.ledger-scroll { overflow: auto; max-width: 100%; border: 1px solid var(--aq-border); border-radius: 6px; }
.ledger-scroll table { width: 100%; min-width: 520px; border-collapse: collapse; text-align: left; font-size: 13px; }
.ledger-scroll th, .ledger-scroll td { padding: 8px; border-bottom: 1px solid var(--aq-border); }
.ledger-scroll th { color: var(--aq-secondary); white-space: nowrap; }
.ledger-scroll .btn { min-height: 32px; white-space: nowrap; }
.ledger-confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: var(--aq-warning-bg); }
.ledger-confirm p { flex: 1 1 100%; margin: 0; overflow-wrap: anywhere; }
.status { margin: 0; overflow-wrap: anywhere; }
.sr-only { position: absolute; width: 1px; height: 1px; clip: rect(0, 0, 0, 0); overflow: hidden; }
@media (max-width: 660px) { .ledger-filter { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 430px) { .ledger-filter, .ledger-targets { grid-template-columns: minmax(0, 1fr); } .ledger-head { align-items: start; } }
</style>
