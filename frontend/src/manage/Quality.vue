<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import QualityEvidence from './QualityEvidence.vue'
import QualityStation from './QualityStation.vue'
import QualityWindow from './QualityWindow.vue'
import { useQualityCommand } from './qualityCommand'
import {
  actionLabels, actionReasons, isDayRange, issueStates, label, numericId, runId, timeText,
  type AuditRow, type Correction, type CursorPage, type Impact, type Issue, type IssuePage, type QualityAction,
  type QualityProductPage, type QualityReceipt, type WindowRow,
} from './qualityContract'

const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const issueRead = usePrivateRead<IssuePage>()
const detailRead = usePrivateRead<Issue>()
const windowRead = usePrivateRead<CursorPage<WindowRow>>()
const directoryRead = usePrivateRead<QualityProductPage>()
const mappingRead = usePrivateRead<Issue>()
const auditRead = usePrivateRead<CursorPage<AuditRow>>()
const command = useQualityCommand('quality')
const busy = command.busy

const product = ref('')
const directoryStart = ref('')
const directoryEnd = ref('')
const directoryError = ref('')
const directoryCursor = ref<string>()
const status = ref('')
const run = ref('')
const windowId = ref('')
const issues = ref<Issue[]>([])
const windows = ref<WindowRow[]>([])
const issueCursor = ref<number | null>(null)
const windowCursor = ref<string | null>(null)
const windowLoaded = ref(false)
const selectedId = ref<number | null>(null)
const selectedWindow = ref<number | null>(null)
const selectedBatch = ref<{ id: number; receipt?: number | null; keyType?: 'city' | 'station' } | null>(null)
const mappingTarget = ref('')
const mappingError = ref('')
const auditStart = ref('')
const auditEnd = ref('')
const auditError = ref('')
const projectionVersion = ref(0)
const filterError = ref('')
const commandError = ref('')
const receipt = ref<QualityReceipt | null>(null)
const confirmAction = ref<QualityAction | null>(null)
const confirmButton = ref<HTMLButtonElement | null>(null)
const detailHeading = ref<HTMLHeadingElement | null>(null)
let confirmTrigger: HTMLElement | null = null
const actions = Object.keys(actionLabels) as QualityAction[]

// Draft fields cannot reinterpret a returned page or its cursor.
const applied = computed(() => ({
  product: numericId(route.query.product),
  status: typeof route.query.status === 'string' && Object.hasOwn(issueStates, route.query.status) ? route.query.status : undefined,
  run: runId(route.query.run),
  window: numericId(route.query.window),
}))
const synthetic = computed(() => session.scope === 'synthetic')
const canReview = computed(() => session.capabilities.actions.includes('review_observation'))
const canAdjudicate = computed(() => canReview.value && session.capabilities.actions.includes('adjudicate_quality'))
const canManageStation = computed(() => canReview.value && synthetic.value && session.capabilities.actions.includes('manage_catalog'))
const selectedIssue = detailRead.data
const waiting = computed(() => selectedId.value == null ? undefined : command.pendingId(selectedId.value))

function clearDetail() {
  detailRead.clear()
  clearMapping()
  clearAudit()
  selectedId.value = null
  selectedWindow.value = null
  selectedBatch.value = null
  confirmAction.value = null
  receipt.value = null
  commandError.value = ''
}

function clearAudit() {
  auditRead.clear()
  auditError.value = ''
}

function clearMapping() {
  mappingTarget.value = ''
  mappingRead.clear()
  mappingError.value = ''
}

function clearReads() {
  clearDetail()
  issueRead.clear()
  windowRead.clear()
  issues.value = []
  windows.value = []
  issueCursor.value = null
  windowCursor.value = null
  windowLoaded.value = false
  filterError.value = ''
}

function clearDirectory() {
  directoryRead.clear()
  directoryCursor.value = undefined
  directoryError.value = ''
}

function clearPrivate() {
  clearDirectory()
  clearReads()
}

function displayError(cause: unknown, fallback: string) {
  if (!(cause instanceof ApiError)) return fallback
  if ([403, 404].includes(cause.status) && cause.code !== 'csrf_failed') return '当前对象或用途不可访问；未披露其内容。'
  if (cause.status === 409 && cause.code !== 'page_changed') return '对象版本或处理状态已变化。已停止提交，请核对回读结果后重新判断。'
  if (cause.code === 'csrf_failed') return '安全校验失败，原操作未自动重放。重新确认时将获取新校验凭据。'
  return cause.message || fallback
}

function inScope(affected: Impact) {
  return affected.product === applied.value.product && affected.synthetic === synthetic.value
}

function readDirectory(cursor?: string) {
  if (!canReview.value || busy.value) return
  clearDirectory()
  if (!isDayRange(directoryStart.value, directoryEnd.value)) {
    directoryError.value = '请填写有效的源地方日期区间，最多 30 天，结束日期不包含在内。'
    return
  }
  directoryCursor.value = cursor
  return directoryRead.run(signal => api.get<QualityProductPage>('admin/catalog/products/', {
    purpose: 'quality', ...scopeQuery(session.scope), start: directoryStart.value, end: directoryEnd.value, cursor,
  }, signal))
}

function chooseProduct(id: number) {
  if (numericId(product.value) !== id || applied.value.product !== id) {
    run.value = ''
    windowId.value = ''
  }
  product.value = String(id)
  void applyFilters()
}

async function readIssues(before?: number) {
  if (!applied.value.product || !canReview.value) return
  if (before == null) issues.value = []
  issueCursor.value = null
  const result = await issueRead.run(signal => api.get<IssuePage>('admin/quality-issues/', {
    ...applied.value, scope: session.scope || undefined, before,
  }, signal))
  if (result) {
    issues.value = [...issues.value, ...result.results.filter(item => inScope(item.affected))]
    issueCursor.value = result.next_before
  } else if (issueRead.error.value) {
    issues.value = []
    if (issueRead.error.value.status === 403 || issueRead.error.value.status === 404) clearDetail()
  }
}

async function readWindows(cursor?: string) {
  if (!applied.value.product || !canReview.value) return
  windows.value = []
  windowCursor.value = null
  windowLoaded.value = false
  const result = await windowRead.run(signal => api.get<CursorPage<WindowRow>>('admin/collection-windows/', {
    product: applied.value.product, scope: session.scope || undefined, page_size: 25, cursor,
  }, signal))
  if (result) {
    windows.value = result.results.filter(row => row.product === applied.value.product && row.synthetic === synthetic.value)
    windowCursor.value = result.page.next_cursor
    windowLoaded.value = true
  }
}

async function readIssue(id: number, keepFeedback = false) {
  if (!canReview.value) return
  if (!keepFeedback) clearDetail()
  else {
    clearMapping()
    clearAudit()
  }
  selectedId.value = id
  selectedBatch.value = null
  selectedWindow.value = null
  confirmAction.value = null
  const detail = await detailRead.run(async signal => {
    const result = await api.get<Issue>(`admin/quality-issues/${id}/`, undefined, signal)
    if (result.id !== id || !inScope(result.affected)) throw new Error('Quality issue context mismatch')
    return result
  })
  if (!detail) {
    if (detailRead.error.value) {
      issues.value = issues.value.filter(row => row.id !== id)
      const status = detailRead.error.value.status
      if (status === 403 || status === 404) receipt.value = null
    }
    return
  }
  issues.value = issues.value.flatMap(item => item.id !== id ? [item] : !applied.value.status || detail.status === applied.value.status
    ? [{ ...item, status: detail.status, event_version: detail.event_version }] : [])
  selectedWindow.value = detail.window
  projectionVersion.value++
  await nextTick()
  detailHeading.value?.focus()
}

async function applyFilters(from?: { window: number; run?: string }) {
  if (busy.value) return
  if (from) {
    product.value = String(applied.value.product ?? '')
    windowId.value = String(from.window)
    run.value = from.run ?? ''
  }
  const id = numericId(product.value), requestedRun = runId(run.value), requestedWindow = numericId(windowId.value)
  if (!id || (run.value && !requestedRun) || (windowId.value && !requestedWindow)) {
    filterError.value = '请输入有效的产品 ID、可选的 Run UUID 和窗口 ID。'
    return
  }
  filterError.value = ''
  const query = { ...scopeQuery(session.scope), product: String(id), status: status.value || undefined, run: requestedRun, window: requestedWindow?.toString() }
  if (id === applied.value.product && query.status === applied.value.status && requestedRun === applied.value.run && requestedWindow === applied.value.window) {
    clearReads()
    selectedWindow.value = requestedWindow ?? null
    await readIssues()
  } else await router.replace({ name: 'manage-quality', query })
}

function openWindow(id: number) {
  clearDetail()
  selectedWindow.value = id
  void nextTick(() => document.getElementById('quality-trace-title')?.focus())
}

function openBatch(id: number, receiptId?: number | null, keyType?: 'city' | 'station') {
  selectedBatch.value = { id, receipt: receiptId, keyType }
  void nextTick(() => document.getElementById('quality-evidence-title')?.focus())
}

function correctionRef(action: Correction) {
  if (action === 'correct_mapping' && mappingTarget.value) {
    return mappingRead.data.value?.evidence?.correct_mapping
  }
  return selectedIssue.value?.evidence?.[action]
}

async function previewMapping() {
  const event = selectedIssue.value, target = mappingTarget.value
  if (!event || !synthetic.value || !canAdjudicate.value || busy.value || waiting.value || event.status === 'resolved') return
  mappingError.value = ''
  if (!target || target.length > 100 || /[\p{C}]/u.test(target)) {
    mappingError.value = '请输入受控交接的有效目标对象代码。'
    return
  }
  const preview = await mappingRead.run(async signal => {
    const result = await api.get<Issue>(`admin/quality-issues/${event.id}/`, { mapping_target: target }, signal)
    if (result.id !== event.id || !inScope(result.affected)) throw new Error('Mapping context mismatch')
    if (result.event_version !== event.event_version) throw new ApiError('事件版本已变化。', 409, 'precondition_conflict')
    return result
  })
  if (selectedIssue.value !== event || mappingTarget.value !== target || !canAdjudicate.value) return
  if (mappingRead.error.value?.status === 409) {
    await readIssue(event.id)
    if (selectedId.value === event.id) mappingError.value = '事件版本已变化，已回读。请重新核验目标。'
  } else if (preview && !preview.evidence?.correct_mapping) {
    mappingError.value = '未取得该目标的可披露签名证据；不会提交更正。'
  }
}

function actionEnabled(action: QualityAction) {
  const event = selectedIssue.value
  if (!canAdjudicate.value || busy.value || waiting.value || !event || event.status === 'resolved') return false
  if (action === 'claim') return event.status === 'open'
  // The server verifies ownership of an in-review event; me/ does not publish an actor ID.
  if (action === 'close' || action === 'hold') return event.status === 'open' || event.status === 'in_review'
  return Boolean(correctionRef(action))
}

async function startAction(action: QualityAction, event: MouseEvent) {
  if (!actionEnabled(action)) return
  confirmTrigger = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  commandError.value = ''
  confirmAction.value = action
  await nextTick()
  confirmButton.value?.focus()
}

function cancelAction() {
  confirmAction.value = null
  confirmTrigger?.focus()
  confirmTrigger = null
}

async function submitAction() {
  const event = selectedIssue.value, action = confirmAction.value
  if (!event || !action || !actionEnabled(action)) return
  const generation = privateStateGeneration()
  const current = () => generation === privateStateGeneration() && selectedId.value === event.id && canAdjudicate.value
  commandError.value = ''
  receipt.value = null
  const result = await command.submit<QualityReceipt>(event.id, `admin/quality-issues/${event.id}/actions/`, {
    action, reason_code: actionReasons[action], expected_version: event.event_version,
    evidence_ref: action === 'correct_source' || action === 'correct_mapping' ? correctionRef(action) ?? null : null,
  }).catch(async cause => {
    if (current() && !command.pendingId(event.id)) await readIssue(event.id, true)
    if (current()) commandError.value = command.pendingId(event.id)
      ? '未收到确定回执。只可查询原始命令，不会生成新请求。'
      : displayError(cause, '裁决未完成。请核对当前事件。')
  })
  if (!current()) return
  confirmAction.value = null
  if (result) {
    receipt.value = { ...result, outcome: result.outcome ?? action }
    await readIssue(event.id, true)
  }
}

async function lookupReceipt() {
  const id = selectedId.value
  if (id == null || !canAdjudicate.value) return
  const generation = privateStateGeneration()
  const current = () => generation === privateStateGeneration() && selectedId.value === id && canAdjudicate.value
  commandError.value = ''
  try {
    const found = await command.lookup(id)
    if (!found || !current()) return
    receipt.value = { action_id: found.action_id, status: found.state as Issue['status'], event_version: found.version, outcome: found.action }
    await readIssue(id, true)
  } catch (cause) {
    if (current()) commandError.value = '尚未取得可披露的原始回执。原命令仍锁定，不可另发新命令。' + (cause instanceof ApiError && cause.code === 'csrf_failed' ? ' 安全校验已失效，请再次查询。' : '')
  }
}

function impactText(item: Impact) {
  return `${item.geography} · ${item.object_code} · ${item.indicator} · ${item.start} 至 ${item.end}（右端不含）`
}

function readAudit(cursor?: string) {
  const event = selectedIssue.value
  if (!event || !canReview.value || busy.value) return
  clearAudit()
  if (!isDayRange(auditStart.value, auditEnd.value)) {
    auditError.value = '请填写有效的 UTC 日期区间，最多 30 天，结束日期不包含在内。'
    return
  }
  const start = `${auditStart.value}T00:00:00Z`, end = `${auditEnd.value}T00:00:00Z`
  return auditRead.run(signal => api.get<CursorPage<AuditRow>>('admin/object-audit/', {
    kind: 'quality_issue', id: event.id, start, end, cursor,
  }, signal))
}

const auditActors = { self: '本人', other: '其他操作者', system: '系统' }
watch([auditStart, auditEnd], clearAudit, { flush: 'sync' })
watch([directoryStart, directoryEnd, synthetic, canReview], clearDirectory, { flush: 'sync' })
watch(mappingTarget, () => {
  mappingRead.clear()
  mappingError.value = ''
  if (confirmAction.value === 'correct_mapping') cancelAction()
}, { flush: 'sync' })
watch(canAdjudicate, allowed => {
  if (!allowed) {
    clearMapping()
    confirmAction.value = null
    receipt.value = null
    commandError.value = ''
  }
})

watch(() => [route.query.product, route.query.status, route.query.run, route.query.window, session.scope, canReview.value], () => {
  if (route.name !== 'manage-quality') return
  clearReads()
  const current = applied.value
  product.value = current.product?.toString() ?? ''
  status.value = current.status ?? ''
  run.value = current.run ?? ''
  windowId.value = current.window?.toString() ?? ''
  if (current.product && canReview.value) {
    selectedWindow.value = current.window ?? null
    void readIssues()
  }
}, { immediate: true })

onMounted(() => window.addEventListener('air-quality:clear-reads', clearPrivate))
onUnmounted(() => window.removeEventListener('air-quality:clear-reads', clearPrivate))
</script>

<template>
  <div class="quality-page page-content">
    <header class="section-head">
      <div><h1>数据质量</h1><p>核对缺口、争议观测与处理回执。事件结案和观测资格分开记录。</p></div>
      <span class="quality-badge">{{ synthetic ? 'F 合成域' : '正式域' }}</span>
    </header>

    <section v-if="canReview" class="panel quality-section" aria-labelledby="quality-filter-title">
      <div class="quality-bar"><div><h2 id="quality-filter-title">筛选质量事件</h2><p>查找获权产品，或使用采集账本交接的产品、Run 和窗口。</p></div></div>
      <details class="quality-directory">
        <summary>查找获权产品</summary>
        <p class="quality-context">以源地方日查找有可披露事件的产品。日期只用于发现产品，不筛选下方事件。</p>
        <form class="quality-fields" @submit.prevent="readDirectory()">
          <label class="field">产品发现起始日期<input v-model="directoryStart" type="date" required /></label>
          <label class="field">产品发现结束日期（不含）<input v-model="directoryEnd" type="date" required /></label>
          <div class="quality-actions"><button class="btn" type="submit" :disabled="busy || directoryRead.busy.value">查询获权产品</button></div>
        </form>
        <p v-if="directoryRead.busy.value" role="status">正在核验获权产品…</p>
        <p v-else-if="directoryError || directoryRead.error.value" class="status status-error" role="alert">{{ directoryError || displayError(directoryRead.error.value, '产品目录读取失败。') }}</p>
        <template v-else-if="directoryRead.data.value">
          <p v-if="!directoryRead.data.value.results.length" class="muted">这段日期没有可披露的质量产品。</p>
          <ul v-else class="quality-options" aria-label="获权质量产品">
            <li v-for="row in directoryRead.data.value.results" :key="row.product">
              <button class="btn quality-small" type="button" :disabled="busy" @click="chooseProduct(row.product)">查看产品 {{ row.product }}（{{ row.shape }}）</button>
              <small>{{ row.source }}</small>
            </li>
          </ul>
          <div v-if="directoryRead.data.value.next_cursor || directoryCursor" class="quality-actions">
            <button v-if="directoryCursor" class="btn quality-small" type="button" :disabled="busy" @click="readDirectory()">产品目录首页</button>
            <button v-if="directoryRead.data.value.next_cursor" class="btn quality-small" type="button" :disabled="busy" @click="readDirectory(directoryRead.data.value.next_cursor)">下一页产品</button>
          </div>
        </template>
      </details>
      <form class="quality-fields" @submit.prevent="applyFilters()">
        <label class="field">产品 ID<input v-model.trim="product" inputmode="numeric" autocomplete="off" required /></label>
        <label class="field">事件状态<select v-model="status"><option value="">全部</option><option v-for="(name, key) in issueStates" :key="key" :value="key">{{ name }}</option></select></label>
        <label class="field">Run UUID<input v-model.trim="run" autocomplete="off" placeholder="可选" /></label>
        <label class="field">窗口 ID<input v-model.trim="windowId" inputmode="numeric" autocomplete="off" placeholder="可选" /></label>
        <div class="quality-actions"><button class="btn primary" type="submit" :disabled="busy">读取事件</button></div>
      </form>
      <p v-if="filterError" class="status status-error" role="alert">{{ filterError }}</p>
    </section>

    <div v-if="canReview" class="quality-layout">
      <section class="panel quality-section" aria-labelledby="quality-list-title" :aria-busy="issueRead.busy.value">
        <div class="quality-bar"><div><h2 id="quality-list-title">获权事件</h2><p v-if="applied.product">产品 {{ applied.product }}<template v-if="applied.window"> · 窗口 {{ applied.window }}</template><template v-if="applied.run"> · 限定 Run {{ applied.run }}</template></p></div></div>
        <p v-if="issueRead.busy.value" role="status">正在读取事件…</p>
        <p v-else-if="issueRead.error.value" class="status status-error" role="alert">{{ displayError(issueRead.error.value, '质量事件读取失败。') }}</p>
        <p v-else-if="!issues.length" class="muted">{{ applied.product ? '没有可披露的质量事件。' : '请先应用产品条件。' }}</p>
        <div v-if="issues.length" class="quality-scroll" role="region" tabindex="0" aria-label="质量事件表">
          <table><caption class="sr-only">质量事件列表</caption><thead><tr><th scope="col">ID</th><th scope="col">事件</th><th scope="col">状态</th><th scope="col">定位</th></tr></thead>
            <tbody><tr v-for="item in issues" :key="item.id" :class="{ 'quality-selected': selectedId === item.id }">
              <th scope="row">{{ item.id }}</th><td>{{ label(item.code) }}<small>{{ label(item.severity) }} · 规则 {{ item.rule_version ?? '未披露' }}</small></td><td>{{ issueStates[item.status] }}</td>
              <td><button class="btn quality-small" type="button" :disabled="busy" :aria-pressed="selectedId === item.id" @click="readIssue(item.id)">查看事件</button></td>
            </tr></tbody>
          </table>
        </div>
        <button v-if="issueCursor != null" class="btn" type="button" :disabled="busy || issueRead.busy.value" @click="readIssues(issueCursor)">读取下一页</button>
      </section>

      <section class="panel quality-section" aria-labelledby="quality-detail-title" :aria-busy="detailRead.busy.value || busy">
        <div class="quality-bar"><div><h2 id="quality-detail-title" ref="detailHeading" tabindex="-1">事件详情与裁决</h2><p>按当前版本核对证据与影响。</p></div><span v-if="selectedIssue" class="quality-badge">v{{ selectedIssue.event_version }}</span></div>
        <p v-if="detailRead.busy.value" role="status">正在回读事件详情…</p>
        <p v-else-if="detailRead.error.value" class="status status-error" role="alert">{{ displayError(detailRead.error.value, '事件详情读取失败。') }}</p>
        <p v-else-if="!selectedIssue" class="muted">选择一个获权事件核对详情。</p>
        <template v-else>
          <dl class="quality-meta">
            <div><dt>事件</dt><dd>{{ selectedIssue.id }} · {{ label(selectedIssue.code) }}</dd></div>
            <div><dt>状态</dt><dd>{{ issueStates[selectedIssue.status] }} · {{ label(selectedIssue.severity) }}</dd></div>
            <div><dt>规则 / 创建时刻</dt><dd>{{ selectedIssue.rule_version ?? '未披露' }} · {{ timeText(selectedIssue.created_at) }}</dd></div>
            <div><dt>处理人</dt><dd>{{ selectedIssue.handler ?? '未领取' }}</dd></div>
            <div><dt>Run / 窗口</dt><dd>{{ selectedIssue.run ?? '未关联' }} / {{ selectedIssue.window ?? '未关联' }}</dd></div>
            <div><dt>批次 / 回执</dt><dd>{{ selectedIssue.batch ?? '未关联' }} / {{ selectedIssue.receipt ?? '未关联' }}</dd></div>
          </dl>
          <div class="quality-chain">
            <h3>受影响范围</h3><p>{{ impactText(selectedIssue.affected) }}</p>
            <p v-if="selectedIssue.affected.observed_at">观测 {{ timeText(selectedIssue.affected.observed_at) }} · {{ selectedIssue.affected.window_definition }} · {{ selectedIssue.affected.standard_series }}</p>
          </div>
          <div class="quality-chain">
            <h3>当前资格与候选</h3>
            <p v-if="selectedIssue.current_revision === undefined" class="muted">当前事件没有观测键投影，需沿窗口或批次核对。</p>
            <template v-else>
              <p>当前修订 {{ selectedIssue.current_revision ?? '无' }} · 来源水位 {{ selectedIssue.watermark ?? '未披露' }} · 资格版本 {{ selectedIssue.qualification_version ?? '未披露' }}</p>
              <p>候选修订：{{ selectedIssue.candidate_revisions?.join('、') || '无' }}</p>
              <p v-if="selectedIssue.disputed" class="status status-warning">当前键仍有争议。普通结案不会清除争议或恢复覆盖 V。</p>
              <p v-else-if="selectedIssue.disputed === false">当前键未标记争议。</p>
            </template>
            <button v-if="selectedIssue.batch" class="btn" type="button" :disabled="busy" @click="openBatch(selectedIssue.batch, selectedIssue.receipt)">读取事件批次</button>
            <p v-else class="muted">事件未关联批次；无法从事件 ID 或修订 ID 推断观测键。</p>
          </div>
          <div class="quality-chain">
            <h3>受控裁决</h3>
            <p v-if="!canAdjudicate" class="muted">当前会话仅可审阅。</p>
            <template v-else>
              <details v-if="synthetic && selectedIssue.status !== 'resolved' && selectedIssue.affected.indicator === 'CO'" class="quality-directory">
                <summary>核验跨键映射目标（F）</summary>
                <p class="quality-context">仅用于受控交接的目标代码。无该目标的签名证据，不能使用原键证据提交。</p>
                <form class="quality-fields" @submit.prevent="previewMapping">
                  <label class="field">目标对象代码<input v-model.trim="mappingTarget" maxlength="100" :disabled="busy || !!waiting" required /></label>
                  <div class="quality-actions"><button class="btn" type="submit" :disabled="busy || !!waiting || mappingRead.busy.value">核验目标</button></div>
                </form>
                <p v-if="mappingRead.busy.value" role="status">正在按目标核验签名证据…</p>
                <p v-else-if="mappingError || mappingRead.error.value" class="status status-error" role="alert">{{ mappingError || displayError(mappingRead.error.value, '未能核验该目标。') }}</p>
                <p v-else-if="mappingRead.data.value?.evidence?.correct_mapping" role="status">已核验此目标的签名证据；提交前仍需确认。</p>
              </details>
              <div class="quality-actions"><button v-for="action in actions" :key="action" class="btn" type="button" :disabled="!actionEnabled(action)" @click="startAction(action, $event)">{{ actionLabels[action] }}<template v-if="(action === 'correct_source' || action === 'correct_mapping') && !correctionRef(action)">（待核）</template></button></div>
              <p class="muted">更正需要获权详情返回的签名证据。无证据时保留待核。</p>
              <div v-if="confirmAction" class="status status-warning" role="group" aria-labelledby="quality-confirm-title">
                <strong id="quality-confirm-title">确认{{ actionLabels[confirmAction] }}</strong>
                <p>事件 {{ selectedIssue.id }} · 版本 v{{ selectedIssue.event_version }} · {{ impactText(selectedIssue.affected) }}。</p>
                <p v-if="confirmAction === 'close'">普通结案只记录审阅决定，不解除争议。</p>
                <p v-if="confirmAction === 'correct_mapping' && mappingTarget">目标对象代码 {{ mappingTarget }}。实际影响范围以回执为准。</p>
                <div class="quality-actions"><button ref="confirmButton" class="btn primary" type="button" :disabled="busy || !!waiting" @click="submitAction">确认提交</button><button class="btn" type="button" :disabled="busy" @click="cancelAction">取消</button></div>
              </div>
            </template>
          </div>
          <details class="quality-chain">
            <summary>查本事件治理经过</summary>
            <p class="quality-context">仅查询本事件和指定时间段，按当前权限披露。</p>
            <form class="quality-fields" @submit.prevent="readAudit()">
              <label class="field">审计起始日期（UTC）<input v-model="auditStart" type="date" required /></label>
              <label class="field">审计结束日期（UTC，不含）<input v-model="auditEnd" type="date" required /></label>
              <div class="quality-actions"><button class="btn" type="submit" :disabled="busy || auditRead.busy.value">读取本事件审计</button></div>
            </form>
            <p v-if="auditRead.busy.value" role="status">正在核验事件与审计权限…</p>
            <p v-else-if="auditError || auditRead.error.value" class="status status-error" role="alert">{{ auditError || displayError(auditRead.error.value, '事件审计读取失败。') }}</p>
            <template v-else-if="auditRead.data.value">
              <p v-if="!auditRead.data.value.results.length" class="muted">此日期区间无可披露的治理记录。</p>
              <div v-else class="quality-scroll" role="region" aria-label="本事件审计" tabindex="0">
                <table><caption class="sr-only">本事件审计</caption><thead><tr><th scope="col">时刻</th><th scope="col">动作 / 结果</th><th scope="col">操作者</th><th scope="col">版本</th></tr></thead><tbody>
                  <tr v-for="(row, index) in auditRead.data.value.results" :key="index"><th scope="row">{{ timeText(row.at) }}</th><td>{{ actionLabels[row.action as QualityAction] ?? label(row.action) }} · {{ label(row.outcome) }}</td><td>{{ auditActors[row.actor_relation] }}</td><td>{{ row.before_version ?? '未披露' }} → {{ row.after_version ?? '未披露' }}</td></tr>
                </tbody></table>
              </div>
              <button v-if="auditRead.data.value.page.next_cursor" class="btn" type="button" :disabled="busy" @click="readAudit(auditRead.data.value.page.next_cursor)">下一页审计</button>
            </template>
          </details>
        </template>
        <p v-if="commandError" class="status status-error" role="alert">{{ commandError }}</p>
        <div v-if="waiting && !busy && canAdjudicate" class="status status-warning">
          <p role="status">请求 {{ waiting }} 的结果未确认，不能另发同一对象的新命令。</p>
          <button class="btn" type="button" @click="lookupReceipt">只查询原始回执</button>
        </div>
        <div v-if="receipt" class="quality-chain" role="status">
          <h3>裁决回执</h3><p>{{ receipt.action_id }} · {{ issueStates[receipt.status] }} · 新事件版本 v{{ receipt.event_version }}</p>
          <p v-if="receipt.outcome === 'close'">结案只记录审阅决定，不调整争议、观测资格或覆盖 V。</p>
          <p v-else-if="receipt.outcome">结果：{{ label(receipt.outcome) }}。以回读的资格和覆盖为准。</p>
          <ul v-if="receipt.affected?.length"><li v-for="(item, index) in receipt.affected" :key="index">{{ impactText(item) }}</li></ul>
        </div>
      </section>
    </div>

    <QualityWindow v-if="canReview && applied.product && selectedWindow" :key="selectedWindow" :id="selectedWindow" :product="applied.product" :synthetic="synthetic" :revision="projectionVersion" @events="(id, rid) => applyFilters({ window: id, run: rid })" @batch="openBatch" />
    <QualityEvidence v-if="canReview && applied.product && selectedBatch" :key="`${projectionVersion}:${selectedBatch.id}:${selectedBatch.receipt ?? ''}:${selectedBatch.keyType ?? ''}`" :product="applied.product" :synthetic="synthetic" :batch="selectedBatch.id" :receipt="selectedBatch.receipt" :key-type="selectedBatch.keyType" :revision="projectionVersion" />

    <section v-if="canReview && applied.product" class="panel quality-section" aria-labelledby="quality-windows-title" :aria-busy="windowRead.busy.value">
      <div class="quality-bar"><div><h2 id="quality-windows-title">获权业务窗口</h2><p>按当前产品查账，窗口覆盖不等于某次 Run 的产出。</p></div><button class="btn" type="button" :disabled="busy || windowRead.busy.value" @click="readWindows()">读取窗口</button></div>
      <p v-if="windowRead.busy.value" role="status">正在读取获权窗口…</p>
      <p v-else-if="windowRead.error.value" class="status status-error" role="alert">{{ displayError(windowRead.error.value, '窗口列表读取失败。') }}</p>
      <p v-else-if="!windows.length" class="muted">{{ windowLoaded ? '没有可披露的业务窗口。' : '选择读取窗口，或从关联事件打开覆盖详情。' }}</p>
      <div v-else class="quality-scroll" role="region" tabindex="0" aria-label="业务窗口表">
        <table><caption class="sr-only">业务窗口列表</caption><thead><tr><th scope="col">ID</th><th scope="col">目标时段（右端不含）</th><th scope="col">范围</th><th scope="col">结果</th><th scope="col">定位</th></tr></thead>
          <tbody><tr v-for="item in windows" :key="item.id"><th scope="row">{{ item.id }}</th><td>{{ timeText(item.target_start) }} 至 {{ timeText(item.target_end) }}</td><td>{{ item.full_scope ? '完整授权' : '部分授权' }}</td><td>{{ label(item.result) }}</td><td><button class="btn quality-small" type="button" :disabled="busy" @click="openWindow(item.id)">查看覆盖</button></td></tr></tbody>
        </table>
      </div>
      <button v-if="windowCursor" class="btn" type="button" :disabled="busy" @click="readWindows(windowCursor)">读取下一页窗口</button>
    </section>

    <QualityStation v-if="canManageStation" />
    <p v-else-if="!canReview" class="panel quality-section">当前会话没有质量审阅权限，不能读取质量事件或观测证据。</p>
  </div>
</template>

<style scoped>
.quality-page { gap: 16px; }
.quality-layout { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 16px; align-items: start; }
.quality-directory { padding: 10px 12px; margin-bottom: 14px; border: 1px solid var(--aq-control-border); border-radius: 8px; }
.quality-directory summary { cursor: pointer; font-weight: 650; }
.quality-directory p { margin: 8px 0; }
.quality-options { padding: 0; margin: 10px 0; display: flex; flex-wrap: wrap; gap: 8px 14px; list-style: none; }
.quality-options li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.quality-options small { color: var(--aq-secondary); }
.quality-badge { flex-shrink: 0; padding: 4px 10px; border: 1px solid var(--aq-control-border); border-radius: 999px; background: #f2f9fd; color: var(--aq-secondary); font-size: 13px; font-weight: 650; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.quality-page :deep(.quality-section) { min-width: 0; padding: 18px; overflow-wrap: anywhere; }
.quality-page :deep(.quality-bar) { display: flex; flex-wrap: wrap; align-items: start; justify-content: space-between; gap: 10px 16px; margin-bottom: 14px; }
.quality-page :deep(.quality-bar h2) { margin: 0 0 3px; }
.quality-page :deep(.quality-bar p), .quality-page :deep(.muted) { margin: 0; color: var(--aq-secondary); font-size: 13px; }
.quality-page :deep(.quality-fields) { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(145px, 100%), 1fr)); gap: 12px; align-items: end; }
.quality-page :deep(.quality-form fieldset) { min-width: 0; display: grid; gap: 12px; border: 1px solid var(--aq-control-border); border-radius: 8px; }
.quality-page :deep(.quality-actions) { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.quality-page :deep(.field select) { width: 100%; min-height: 42px; padding: 8px 9px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: #f8fcff; color: var(--aq-text); }
.quality-page :deep(.quality-scroll) { margin-bottom: 10px; overflow-x: auto; }
.quality-page :deep(table) { width: 100%; border-collapse: collapse; font-size: 13px; }
.quality-page :deep(caption) { padding: 5px 0; text-align: left; font-weight: 650; }
.quality-page :deep(th), .quality-page :deep(td) { padding: 8px; border-bottom: 1px solid var(--aq-border); text-align: left; vertical-align: top; }
.quality-page :deep(th) { color: var(--aq-secondary); white-space: nowrap; }
.quality-page :deep(td small), .quality-page :deep(th small) { display: block; color: var(--aq-secondary); }
.quality-page :deep(.quality-selected) { background: #e2f0fa; }
.quality-page :deep(.quality-small) { min-height: 34px; padding-inline: 9px; font-size: 13px; white-space: nowrap; }
.quality-page :deep(.quality-meta) { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(132px, 100%), 1fr)); gap: 8px; margin: 0 0 12px; }
.quality-page :deep(.quality-meta > div) { padding: 9px; border: 1px solid var(--aq-border); border-radius: 8px; }
.quality-page :deep(.quality-meta dt) { color: var(--aq-secondary); font-size: 12px; }
.quality-page :deep(.quality-meta dd) { margin: 3px 0 0; font-weight: 620; }
.quality-page :deep(.coverage dd) { font-size: 21px; font-variant-numeric: tabular-nums; }
.quality-page :deep(.quality-chain) { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--aq-border); }
.quality-page :deep(.quality-chain h3) { margin: 0 0 7px; font-size: 15px; }
.quality-page :deep(.quality-chain p) { margin: 4px 0 8px; }
.quality-chain > summary { cursor: pointer; font-weight: 650; }
.quality-page :deep(.quality-context) { color: var(--aq-secondary); font-size: 13px; }
@media (max-width: 1080px) { .quality-layout { grid-template-columns: 1fr; } }
@media (max-width: 640px) { .quality-page :deep(.quality-section) { padding: 13px; } }
</style>
