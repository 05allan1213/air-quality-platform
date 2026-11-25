<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import {
  buildHistoryImportCommand, errorText, isUuid, knownCollectionRejection, label,
  ledgerPath, nextCursor, type CursorPage, type HistoryAttempt, type HistorySubmission,
} from './collectionLedger'

const props = defineProps<{ canImport: boolean }>()
const emit = defineEmits<{ submitted: [attemptId: string] }>()
const attempts = usePrivateRead<CursorPage<HistoryAttempt>>()
const detail = usePrivateRead<HistoryAttempt>()
const { data: page, busy: loading, error: listError } = attempts
const { data: attempt, busy: loadingDetail, error: detailError } = detail
const stageId = ref('')
const confirmedStage = ref('')
const attemptId = ref('')
const receiptId = ref('')
const receiptDispatchUnconfirmed = ref(false)
const busy = ref(false)
const gateKey = 'aq-history-import-pending-v1'
const gate = ref<SubmissionGate>(readGate())
const feedback = ref('')
let controller: AbortController | undefined

type SubmissionGate = 'ready' | 'pending' | 'unavailable'

function readGate(): SubmissionGate {
  try {
    return window.sessionStorage.getItem(gateKey) === null ? 'ready' : 'pending'
  } catch {
    return 'unavailable'
  }
}

function beginSubmission() {
  gate.value = readGate()
  if (gate.value !== 'ready') return false
  // Keep only a tab-level marker. Private IDs and responses never survive invalidation.
  try {
    window.sessionStorage.setItem(gateKey, '1')
    gate.value = 'pending'
    return true
  } catch {
    gate.value = 'unavailable'
    return false
  }
}

function settleSubmission() {
  try {
    window.sessionStorage.removeItem(gateKey)
    gate.value = 'ready'
  } catch {
    gate.value = readGate()
  }
}

function clear() {
  controller?.abort()
  controller = undefined
  busy.value = false
  stageId.value = ''
  confirmedStage.value = ''
  attemptId.value = ''
  receiptId.value = ''
  receiptDispatchUnconfirmed.value = false
  gate.value = readGate()
  feedback.value = ''
  attempts.clear()
  detail.clear()
}
window.addEventListener('air-quality:clear-reads', clear)
onScopeDispose(() => { window.removeEventListener('air-quality:clear-reads', clear); clear() })
watch(() => props.canImport, clear, { flush: 'sync' })

function load(cursor?: string) {
  detail.clear()
  return attempts.run(signal => api.get<CursorPage<HistoryAttempt>>('admin/history-imports/', { page_size: 25, cursor }, signal))
}

async function read(id: string, href = `/api/v1/admin/history-imports/${id}/`) {
  if (!isUuid(id)) return
  const path = ledgerPath(href, `admin/history-imports/${id}/`)
  if (!path) return
  attemptId.value = id
  const generation = privateStateGeneration()
  const result = await detail.run(async signal => {
    const result = await api.get<HistoryAttempt>(path, undefined, signal)
    if (result.id !== id) throw new ApiError('导入状态与所选尝试不一致。', 502, 'invalid_response')
    return result
  })
  if (receiptId.value === id && generation === privateStateGeneration()) {
    if (result && receiptDispatchUnconfirmed.value
      && (result.dispatch === 'sent' || ['succeeded', 'duplicate', 'rejected', 'failed'].includes(result.state))) {
      settleSubmission()
    } else if (detailError.value && [403, 404].includes(detailError.value.status)) {
      attemptId.value = ''
      receiptId.value = ''
      receiptDispatchUnconfirmed.value = false
    }
  }
}

function recordReceipt(id: string, dispatchUnconfirmed: boolean) {
  receiptId.value = id
  receiptDispatchUnconfirmed.value = dispatchUnconfirmed
  stageId.value = ''
  emit('submitted', id)
  void read(id)
}

function prepare() {
  if (props.canImport && !busy.value && gate.value === 'ready' && isUuid(stageId.value)) confirmedStage.value = stageId.value
}

async function submit() {
  const stage = confirmedStage.value
  if (!props.canImport || busy.value || gate.value !== 'ready' || !isUuid(stage) || stage !== stageId.value) return
  confirmedStage.value = ''
  if (!beginSubmission()) return
  receiptId.value = ''
  receiptDispatchUnconfirmed.value = false
  feedback.value = ''
  detail.clear()
  attempts.clear()
  busy.value = true
  const active = new AbortController()
  const generation = privateStateGeneration()
  const current = () => !active.signal.aborted && generation === privateStateGeneration()
  controller = active
  try {
    const result = await api.post<HistorySubmission>('admin/history-imports/', buildHistoryImportCommand(stage), active.signal)
    if (!current()) return
    if (!result || typeof result.attempt_id !== 'string' || !isUuid(result.attempt_id)) throw new ApiError('导入回执格式无效。', 502, 'invalid_response')
    settleSubmission()
    recordReceipt(result.attempt_id, false)
  } catch (cause) {
    if (!current()) return
    if (cause instanceof ApiError && cause.status === 503 && cause.code === 'dispatch_unconfirmed' && cause.attemptId && isUuid(cause.attemptId)) {
      recordReceipt(cause.attemptId, true)
    } else if (knownCollectionRejection(cause)) {
      settleSubmission()
      feedback.value = errorText(cause, '无法提交此导入。')
    } else {
      stageId.value = ''
      void load()
    }
  } finally {
    if (controller === active) { controller = undefined; busy.value = false }
  }
}
</script>

<template>
  <section class="history-panel" aria-labelledby="collection-history-title" :aria-busy="busy || loading || loadingDetail">
    <div><h3 id="collection-history-title">历史导入</h3><p class="history-muted">使用主机已暂存且获准的 UCI 501 历史资料。此处提供提交和状态查询。</p></div>
    <form v-if="canImport" class="history-form" @submit.prevent="prepare">
      <label class="field">暂存批次 UUID<input v-model.trim="stageId" autocomplete="off" spellcheck="false" required :disabled="busy || gate !== 'ready'" @input="confirmedStage = ''" /></label>
      <button class="btn primary" type="submit" :disabled="busy || gate !== 'ready' || !isUuid(stageId)">准备导入</button>
    </form>
    <div v-if="confirmedStage" class="history-confirm" role="group" aria-label="确认历史导入">
      <p>确认提交暂存批次 <code>{{ confirmedStage }}</code>？</p>
      <button class="btn primary" type="button" @click="submit">确认导入</button>
      <button class="btn" type="button" @click="confirmedStage = ''">取消</button>
    </div>
    <p v-if="feedback" class="status status-error" role="alert">{{ feedback }}</p>
    <p v-if="canImport && gate === 'unavailable'" class="status status-warning" role="status">当前标签页不能保存提交校验状态，导入提交已停用。</p>
    <p v-if="canImport && gate === 'pending' && !busy && !receiptId" class="status status-warning" role="status">原导入结果待核，当前标签页暂停提交。请查看获权导入记录或联系运维。</p>
    <p v-if="receiptId" class="status" :class="receiptDispatchUnconfirmed ? 'status-warning' : 'status-success'" role="status">{{ receiptDispatchUnconfirmed ? '导入已受理，受理时投递未确认。' : '导入已受理。' }}尝试 <code>{{ receiptId }}</code></p>
    <div v-if="receiptId && receiptDispatchUnconfirmed" class="history-actions"><button class="btn" type="button" :disabled="busy || loadingDetail" @click="read(receiptId)">查原尝试状态</button></div>
    <form class="history-form" @submit.prevent="read(attemptId)">
      <label class="field">导入尝试 UUID<input v-model.trim="attemptId" autocomplete="off" spellcheck="false" required @input="detail.clear()" /></label>
      <button class="btn" type="submit" :disabled="busy || loadingDetail || !isUuid(attemptId)">查看尝试状态</button>
    </form>
    <p v-if="loadingDetail" role="status">正在读取导入状态…</p>
    <p v-if="detailError" class="status status-error" role="alert">{{ errorText(detailError, '无法读取此导入。') }}</p>
    <dl v-if="attempt" class="history-detail">
      <div><dt>导入状态</dt><dd>{{ label(attempt.state) }}</dd></div>
      <div><dt>投递状态</dt><dd>{{ label(attempt.dispatch) }}</dd></div>
      <div><dt>提交时间</dt><dd>{{ attempt.created_at }}</dd></div>
      <div><dt>开始时间</dt><dd>{{ attempt.started_at ?? '未记录' }}</dd></div>
      <div><dt>结束时间</dt><dd>{{ attempt.finished_at ?? '未记录' }}</dd></div>
    </dl>
    <div class="history-actions"><button class="btn" type="button" :disabled="busy || loading" @click="load()">读取获权导入记录</button></div>
    <p v-if="loading" role="status">正在读取导入记录…</p>
    <p v-if="listError" class="status status-error" role="alert">{{ errorText(listError, '无法读取导入记录。') }}</p>
    <p v-else-if="page && !page.results.length" class="history-muted">没有可披露的导入记录。</p>
    <ul v-if="page?.results.length" class="history-list">
      <li v-for="item in page.results" :key="item.id">
        <span>{{ item.created_at }} · {{ label(item.state) }}<br /><code>{{ item.id }}</code></span>
        <button class="btn" type="button" :disabled="busy || loadingDetail" @click="read(item.id, item.links.detail)">查看记录</button>
      </li>
    </ul>
    <div v-if="nextCursor(page)" class="history-actions"><button class="btn" type="button" :disabled="busy || loading" @click="load(nextCursor(page) ?? undefined)">下一页导入记录</button></div>
  </section>
</template>

<style scoped>
.history-panel { display: grid; gap: 12px; min-width: 0; overflow-wrap: anywhere; }
.history-panel h3, .history-panel p { margin: 0; }
.history-muted, .history-detail dt { color: var(--aq-muted); font-size: 13px; }
.history-form { display: grid; grid-template-columns: minmax(0, 27rem) auto; align-items: end; justify-content: start; gap: 8px; }
.history-confirm, .history-actions, .history-list li { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.history-confirm { padding: 10px; border-radius: 8px; background: var(--aq-warning-bg); }
.history-confirm p { flex-basis: 100%; }
.history-detail { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(160px, 100%), 1fr)); gap: 8px; margin: 0; }
.history-detail > div { min-width: 0; padding: 8px; border: 1px solid var(--aq-border); border-radius: 6px; }
.history-detail dd { margin: 3px 0 0; }
.history-list { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.history-list li { justify-content: space-between; padding: 8px; border-bottom: 1px solid var(--aq-border); }
@media (max-width: 480px) { .history-form { grid-template-columns: minmax(0, 1fr); justify-items: start; } .history-form .field { width: 100%; } }
</style>
