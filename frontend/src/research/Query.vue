<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Download, Search } from '@element-plus/icons-vue'
import { ApiError, api, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import { useSessionResumeDraft } from '../stores/sessionResumeDraft'
import QueryResultTable from './QueryResultTable.vue'
import QueryTimeDialog, { type QueryTimeResume } from './QueryTimeDialog.vue'
import {
  allowedIndicators, allowedWindows, canonicalDetail, catalogAnchor, csvFields, csvFieldLabels,
  detailPageSize, indexed, matchesWholeQueryObject, maxQueryHours, normalizedInstant, objectId, objectKey, objectName,
  parseWholeQueryIntent, qualities, unitFor, validDate, windows,
  type CatalogObject, type CatalogPage, type CsvField, type DetailPage, type DetailParent, type ExactResponse,
  type ExportResponse, type ExportTicket, type ObservationPage, type ObservationRow,
  type ObservationWindow, type QueryMode, type QueryQuality,
} from './queryContract'

const session = useSessionStore()
const route = useRoute()
const directoryRead = usePrivateRead<CatalogPage>()
const observationRead = usePrivateRead<ObservationPage>()
const detailRead = usePrivateRead<DetailPage>()
const exactRead = usePrivateRead<ExactResponse>()
const ticketRead = usePrivateRead<ExportResponse>()
const downloadRead = usePrivateRead<boolean>()
const options = ref<CatalogObject[]>([])
const catalogCursor = ref<string | null>(null)
const selectedKey = ref('')
const extras = ref<string[]>([])
const mode = ref<QueryMode>('window')
const windowKind = ref<ObservationWindow>('hourly_label_unspecified')
const indicators = ref<string[]>([])
const quality = ref<QueryQuality>('all')
const unit = ref('')
const standard = ref('')
const start = ref('')
const end = ref('')
const asOf = ref('')
const reportDate = ref('')
const fields = ref<CsvField[]>([])
const formError = ref('')
const handoffError = ref('')
const exportNotice = ref('')
const queryError = ref('')
const confirmOpen = ref(false)
const timeDialogOpen = ref(false)
const timeDialogTrigger = ref<HTMLElement | null>(null)
const timeDialog = ref<InstanceType<typeof QueryTimeDialog> | null>(null)
const timeDialogResume = ref<QueryTimeResume | null>(null)
watch(timeDialogOpen, visible => { if (!visible) timeDialogResume.value = null })
const exportScopeHeading = ref<HTMLElement | null>(null)
const previewButton = ref<HTMLElement | null>(null)
const nextDownload = ref<HTMLElement | null>(null)
const exactHeading = ref<HTMLElement | null>(null)
let exactTrigger: HTMLElement | null = null

const object = computed(() => options.value.find(option => objectKey(option) === selectedKey.value))
const sameProduct = computed(() => options.value.filter(option => option.product === object.value?.product && objectKey(option) !== selectedKey.value))
const daily = computed(() => object.value?.shape === 'D')
const selectionWindows = computed(() => object.value ? allowedWindows(object.value.shape) : [])
const selectionIndicators = computed(() => object.value ? allowedIndicators(object.value.shape, windowKind.value) : [])
const selectedUnits = computed(() => [...new Set(indicators.value.map(unitFor))])
const needsStandard = computed(() => indicators.value.some(indexed))
const defaultStandard = computed(() => needsStandard.value ? (daily.value ? 'unknown' : '') : 'not_applicable')
const isWhole = computed(() => mode.value === 'detail' && !daily.value)
const isLatest = computed(() => mode.value === 'latest_valid' && !daily.value)
const description = computed(() => !object.value ? '' : `${object.value.shape} · ${object.value.source}`)
const scope = computed(() => session.scope || undefined)
const hasHandoff = computed(() => route.query.intent !== undefined)

const applied = ref<{
  mode: QueryMode | 'daily'
  product: CatalogObject['shape']
  source: string
  objects: CatalogObject[]
  condition: string
  fingerprint: string
  query?: Record<string, unknown>
  parent?: DetailParent
} | null>(null)
const detailParent = ref<DetailParent | null>(null)
const detailVersion = ref('')
const cursors = ref<(string | undefined)[]>([])
const pageIndex = ref(0)
const exactOpen = ref(false)
const ticket = computed(() => {
  const issued = ticketRead.data.value
  return issued && 'id' in issued ? issued : null
})
const fingerprint = computed(() => JSON.stringify([selectedKey.value, extras.value, mode.value,
  windowKind.value, indicators.value, quality.value, unit.value, standard.value, start.value, end.value, asOf.value, reportDate.value]))
const dirty = computed(() => Boolean(applied.value && applied.value.fingerprint !== fingerprint.value))
const rows = computed(() => detailRead.data.value?.results ?? observationRead.data.value?.results ?? [])
const page = computed(() => detailRead.data.value?.page ?? observationRead.data.value?.page)
const resultState = computed(() => detailRead.data.value?.state ?? observationRead.data.value?.state)
const queryBusy = computed(() => observationRead.busy.value || detailRead.busy.value)
const resultError = computed(() => queryError.value || detailRead.error.value?.message || observationRead.error.value?.message)
const exportError = computed(() => downloadRead.error.value || ticketRead.error.value)
const exactResult = computed(() => exactRead.data.value?.results[0] ?? null)
const wholeCount = computed(() => detailRead.data.value && !detailRead.data.value.page.next_cursor
  ? pageIndex.value * detailPageSize + detailRead.data.value.results.length : null)
const readonlyExport = computed(() => ticketRead.busy.value || downloadRead.busy.value)

function clearExport() {
  confirmOpen.value = false
  exportNotice.value = ''
  ticketRead.clear()
  downloadRead.clear()
}
function clearExact() {
  exactOpen.value = false
  exactTrigger = null
  exactRead.clear()
}
function clearApplied() {
  applied.value = null
  observationRead.clear()
  detailRead.clear()
  detailParent.value = null
  detailVersion.value = ''
  cursors.value = []
  queryError.value = ''
  clearExport()
  clearExact()
}
function clearPrivate() {
  timeDialogOpen.value = false
  clearApplied()
  directoryRead.clear()
  options.value = []
  catalogCursor.value = null
  selectedKey.value = ''
  extras.value = []
  indicators.value = []
  windowKind.value = 'hourly_label_unspecified'
  quality.value = 'all'
  unit.value = ''
  standard.value = ''
  start.value = ''
  end.value = ''
  asOf.value = ''
  reportDate.value = ''
  fields.value = []
  mode.value = 'window'
  formError.value = ''
  handoffError.value = ''
}
window.addEventListener('air-quality:clear-reads', clearPrivate)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', clearPrivate))

const inlineTimeIds = new Set(['query-start', 'query-end', 'query-as-of', 'query-report-date', 'query-time-trigger'])

function safeTimeText(value: string) {
  return /^[\dTZ+:. -]{0,40}$/i.test(value) ? value : ''
}

function captureQueryDraft() {
  const item = object.value
  const id = item && objectId(item)
  if (!item || id === undefined) return null
  const editor = timeDialog.value?.capture()
  const active = document.activeElement?.id ?? ''
  return {
    product: item.product, id, mode: mode.value, window: windowKind.value,
    start: safeTimeText(start.value), end: safeTimeText(end.value), asOf: safeTimeText(asOf.value),
    reportDate: safeTimeText(reportDate.value), inlineFocus: inlineTimeIds.has(active) ? active : '',
    editor: Boolean(editor), editorWindow: editor?.window ?? null, editorFocus: editor?.focus ?? '',
    editorStart: safeTimeText(editor?.start ?? ''), editorEnd: safeTimeText(editor?.end ?? ''),
    editorAsOf: safeTimeText(editor?.asOf ?? ''), editorReportDate: safeTimeText(editor?.reportDate ?? ''),
  }
}

type QueryResume = NonNullable<ReturnType<typeof captureQueryDraft>>
let resumeDraft = useSessionResumeDraft(captureQueryDraft)

function restoreQueryDraft(item: CatalogObject, draft: QueryResume) {
  const key = objectKey(item)
  if (selectedKey.value !== key) {
    selectedKey.value = key
    pickObject()
  }
  mode.value = daily.value ? 'window' : draft.mode
  useTimeDraft({ window: selectionWindows.value.includes(draft.window) ? draft.window : windowKind.value,
    start: draft.start, end: draft.end, asOf: draft.asOf, reportDate: draft.reportDate })
  void nextTick(() => {
    if (selectedKey.value !== key) return
    if (draft.editor && draft.editorWindow) {
      timeDialogResume.value = { window: draft.editorWindow, start: draft.editorStart, end: draft.editorEnd,
        asOf: draft.editorAsOf, reportDate: draft.editorReportDate, focus: draft.editorFocus }
      timeDialogOpen.value = true
    } else if (inlineTimeIds.has(draft.inlineFocus) && document.activeElement === document.body) {
      document.getElementById(draft.inlineFocus)?.focus()
    }
  })
}

async function locateResumeObject(draft: QueryResume, dataScope: string, signal: AbortSignal) {
  let cursor: string | undefined
  while (!signal.aborted) {
    const page = await api.get<CatalogPage>('catalog/objects/', {
      scope: dataScope || undefined, product: draft.product, page_size: 100, cursor,
    }, signal)
    const item = page.results.find(entry => (objectId(entry) ?? 0) >= draft.id)
    if (item) return item.product === draft.product && objectId(item) === draft.id ? item : undefined
    if (!page.page.next_cursor || page.page.next_cursor === cursor) return
    cursor = page.page.next_cursor
  }
}

async function readDirectory(cursor?: string) {
  const draft = !cursor ? resumeDraft : null
  if (!cursor) {
    resumeDraft = null
    clearPrivate()
  }
  const dataScope = session.scope
  const intent = hasHandoff.value ? parseWholeQueryIntent(route.query, dataScope) : null
  if (hasHandoff.value && !intent) {
    handoffError.value = '无法带入该整组明细条件。请重新选择查询条件。'
    return
  }
  const available = (item: CatalogObject) => item.synthetic === Boolean(dataScope) &&
    objectId(item) !== undefined && Boolean(catalogAnchor(item, dataScope))
  let restoredObject: CatalogObject | undefined
  const matchingResume = (entry: CatalogObject) => draft && entry.product === draft.product && objectId(entry) === draft.id && available(entry)
  const result = await directoryRead.run(async signal => {
    const page = await api.get<CatalogPage>('catalog/objects/', {
      scope: dataScope || undefined, product: intent?.product, page_size: intent ? 100 : 50, cursor,
    }, signal)
    if (!draft) return page
    restoredObject = page.results.find(matchingResume)
    if (!intent && !restoredObject && page.page.next_cursor) {
      const item = await locateResumeObject(draft, dataScope, signal)
      if (item && matchingResume(item)) restoredObject = item
    }
    return page
  })
  if (!result) {
    if (directoryRead.error.value && [403, 409, 410].includes(directoryRead.error.value.status)) {
      const error = directoryRead.error.value
      clearPrivate()
      directoryRead.error.value = error
    }
    return
  }
  catalogCursor.value = result.page.next_cursor
  if (intent) {
    const item = result.results.find(entry => available(entry) && matchesWholeQueryObject(intent, entry))
    if (!item) {
      // Product-filtered pages are ordered by object ID.
      if (result.results.some(entry => (objectId(entry) ?? 0) >= intent.object)) catalogCursor.value = null
      if (!catalogCursor.value) handoffError.value = '当前获准目录无法匹配这组条件。请重新选择查询条件。'
      return
    }
    catalogCursor.value = null
    options.value = [item]
    selectedKey.value = objectKey(item)
    pickObject()
    mode.value = 'detail'
    windowKind.value = intent.window
    indicators.value = [intent.indicator]
    standard.value = intent.standard
    start.value = intent.start
    end.value = intent.end
    if (draft && restoredObject === item) restoreQueryDraft(item, draft)
    return
  }
  const known = new Set(options.value.map(objectKey))
  options.value.push(...result.results.filter(item => available(item) && !known.has(objectKey(item))))
  const match = restoredObject
  if (draft && match) {
    if (!options.value.some(item => objectKey(item) === objectKey(match))) options.value.push(match)
    restoreQueryDraft(match, draft)
  }
}
watch(() => [route.name, route.query, session.scope, session.scopeInitialized, session.authenticated], () => {
  if (route.name === 'query' && session.scopeInitialized && session.authenticated) void readDirectory()
}, { immediate: true })

function pickObject() {
  clearApplied()
  extras.value = []
  asOf.value = ''
  quality.value = 'all'
  unit.value = ''
  formError.value = ''
  const selected = object.value
  const anchor = selected ? catalogAnchor(selected, session.scope) : null
  if (!selected || !anchor) return
  if (daily.value) mode.value = 'window'
  windowKind.value = anchor.window
  indicators.value = [selected.indicator]
  standard.value = anchor.standard
  start.value = anchor.start
  end.value = anchor.end
  reportDate.value = anchor.date
}
function pickWindow() {
  indicators.value = indicators.value.filter(indicator => selectionIndicators.value.includes(indicator))
  if (!indicators.value.length) indicators.value = [selectionIndicators.value[0]]
  unit.value = ''
  standard.value = defaultStandard.value
}
function useTimeDraft(draft: { window: ObservationWindow; start: string; end: string; asOf: string; reportDate: string }) {
  if (windowKind.value !== draft.window) {
    windowKind.value = draft.window
    pickWindow()
  }
  start.value = draft.start
  end.value = draft.end
  asOf.value = draft.asOf
  reportDate.value = draft.reportDate
}
function pickIndicators() {
  if (daily.value) indicators.value = indicators.value.slice(-1)
  unit.value = ''
  standard.value = defaultStandard.value
}
watch(fingerprint, () => {
  formError.value = ''
  if (dirty.value) clearExport()
  if (dirty.value && exactOpen.value) clearExact()
})
watch(fields, () => {
  exportNotice.value = ''
  ticketRead.clear()
  downloadRead.clear()
})

function getSelection() {
  if (!object.value || !indicators.value.length) return null
  const selected = object.value
  const objects = [selected, ...sameProduct.value.filter(item => extras.value.includes(objectKey(item)))]
  if (objects.length > 5) return null
  const ids = objects.map(objectId).filter((id): id is number => id !== undefined).sort((a, b) => a - b)
  return {
    selected, objects, ids,
    entity: selected.site_epoch ? 'site_epochs' as const : 'cities' as const,
    indicators: [...indicators.value].sort(),
    report: windows[windowKind.value].report,
  }
}

function apply() {
  clearApplied()
  const selection = getSelection()
  if (!selection) {
    formError.value = '请选择同一产品下的 1–5 个对象，并至少选择一项指标。'
    return
  }
  const { selected, objects, ids, entity, indicators: chosen, report } = selection
  const base = { product: selected.product, scope: scope.value }
  const common = { window_definition: windowKind.value, report_kind: report }
  const info = { product: selected.shape, source: selected.source, objects, fingerprint: fingerprint.value }
  const label = `${chosen.join('、')} · ${windows[windowKind.value].label}`
  if (daily.value) {
    if (!validDate(reportDate.value) || (needsStandard.value && !standard.value)) {
      formError.value = '请选择源地方报告日及该指标的标准系列。'
      return
    }
    applied.value = { ...info, mode: 'daily', condition: `${label} · ${reportDate.value}` }
    openExact({ ...base, ...common, city: ids[0], indicator: chosen[0], local_report_date: reportDate.value,
      standard_series: standard.value || 'not_applicable' })
    return
  }
  const since = normalizedInstant(start.value)
  const until = normalizedInstant(end.value)
  const sinceTime = since ? Date.parse(since) : 0
  const untilTime = until ? Date.parse(until) : 0
  const as_of = asOf.value.trim() ? normalizedInstant(asOf.value) : undefined
  if (!isLatest.value && (!since || !until || sinceTime >= untilTime)) {
    formError.value = '请输入带时区的开始和结束时刻，结束时刻须晚于开始时刻。'
    return
  }
  if (isLatest.value && as_of === null) {
    formError.value = '截止时刻须带时区。留空时使用服务评估时刻。'
    return
  }
  if (!isLatest.value) {
    const hours = Math.ceil((untilTime - sinceTime) / 3_600_000)
    if (hours > maxQueryHours || hours * ids.length * chosen.length > (isWhole.value ? 12_000 : 3600)) {
      formError.value = '请求超过窗口或数据格数限制，请缩短时段或减少对象及指标。'
      return
    }
  }
  const condition = `${label} · ${isLatest.value ? '截至所选时刻的最近有效' : `[${since}, ${until})`}`
  if (isWhole.value && since && until) {
    if (needsStandard.value && !standard.value) {
      formError.value = '整组明细须为 AQI 或等级指标明确指定标准系列。'
      return
    }
    const parent: DetailParent = { method: 'detail', method_version: 's05.v1', grain: 'hour', selection: {
      ...common, product: selected.product, scope: scope.value ?? 'formal', source: selected.source,
      [entity]: ids, indicators: chosen, start: since, end: until, quality: 'reliable',
      units: Object.fromEntries(chosen.map(indicator => [indicator, unitFor(indicator)])),
      standard_series: Object.fromEntries(chosen.map(indicator => [indicator, indexed(indicator) ? standard.value : 'not_applicable'])),
    } }
    applied.value = { ...info, mode: 'detail', condition, parent }
  } else {
    const sameStandard = !needsStandard.value || indicators.value.every(indexed)
    const params = { ...base, ...common, source: selected.source, [entity]: ids.join(','), indicators: chosen.join(','),
      mode: mode.value, quality: isLatest.value ? 'valid' : quality.value, unit: selectedUnits.value.length === 1 ? unit.value || undefined : undefined,
      standard_series: sameStandard ? standard.value || undefined : undefined,
      ...(isLatest.value ? { as_of } : { start: since, end: until }), page_size: 25 }
    applied.value = { ...info, mode: mode.value, condition, query: params }
  }
  void loadPage(0)
}

async function loadPage(index: number, cursor?: string) {
  const frozen = applied.value
  if (!frozen || queryBusy.value) return
  pageIndex.value = index
  clearExact()
  clearExport()
  queryError.value = ''
  if (frozen.mode === 'detail' && frozen.parent) {
    const parent = detailParent.value ?? frozen.parent
    const version = detailVersion.value || undefined
    if (cursor && !version) return
    const result = await detailRead.run(signal => api.post<DetailPage>('analysis/details/', {
      ...parent, input_version: version, support: 'observed', page_size: detailPageSize, cursor,
    }, signal))
    if (applied.value !== frozen) return
    const canonical = result ? canonicalDetail(result, parent) : null
    if (!canonical || !result || (version && version !== result.input_version)) {
      if (result) queryError.value = '服务返回的范围或版本与本次明细查询不一致，请从首页重查。'
      detailRead.data.value = null
      detailParent.value = null
      detailVersion.value = ''
      clearExport()
      return
    }
    detailParent.value = canonical
    detailVersion.value = result.input_version
  } else if (frozen.query) {
    await observationRead.run(signal => api.get<ObservationPage>('observations/query/', { ...frozen.query, cursor }, signal))
    if (applied.value !== frozen || observationRead.error.value) return
  }
  cursors.value[index] = cursor
}
function restart() {
  cursors.value = []
  detailParent.value = null
  detailVersion.value = ''
  clearExport()
  void loadPage(0)
}

function openExact(parameters: Record<string, unknown>, event?: MouseEvent) {
  exactTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : null
  exactOpen.value = true
  void exactRead.run(signal => api.get<ExactResponse>('observations/current/', parameters, signal))
  void nextTick(() => exactHeading.value?.focus())
}
function inspect(row: ObservationRow, event: MouseEvent) {
  const id = row.site_epoch ?? row.city
  if (!id || !row.observed_at) return
  openExact({ product: row.product, scope: scope.value, [row.site_epoch ? 'site_epoch' : 'city']: id,
    indicator: row.indicator, observed_at: row.observed_at, report_kind: windows[row.window_definition].report,
    window_definition: row.window_definition, standard_series: row.standard_series }, event)
}
function closeExact() {
  const trigger = exactTrigger
  clearExact()
  trigger?.focus()
}
function beginWhole() {
  clearApplied()
  mode.value = 'detail'
  void nextTick(() => document.getElementById('query-start')?.focus())
}
function reviewExport() {
  if (!detailParent.value || !detailVersion.value || dirty.value || resultState.value !== 'ok') return
  confirmOpen.value = true
  exportNotice.value = ''
  void nextTick(() => exportScopeHeading.value?.focus())
}
function cancelExport() {
  clearExport()
  void nextTick(() => previewButton.value?.focus())
}
function invalidateVersion(error: ApiError | null) {
  if (error?.status !== 409) return
  detailRead.clear()
  detailParent.value = null
  detailVersion.value = ''
  confirmOpen.value = false
  ticketRead.clear()
  exportNotice.value = '整组输入版本已变化。请从第一页重新查询，再核对范围和申请新票。'
}
async function prepare() {
  const parent = detailParent.value
  const input_version = detailVersion.value
  if (!parent || !input_version || !confirmOpen.value || readonlyExport.value || dirty.value) return
  const result = await ticketRead.run(signal => api.post<ExportResponse>('me/exports/', {
    kind: 'detail', groups: [{ ...parent, input_version }], fields: [...fields.value],
  }, signal))
  if (!result) {
    invalidateVersion(ticketRead.error.value)
    return
  }
  if ('state' in result) exportNotice.value = '这个整组范围没有可导出的记录。'
  else void nextTick(() => nextDownload.value?.focus())
}
async function download(issued: ExportTicket) {
  if (readonlyExport.value || dirty.value) return
  const activeScope = scope.value ?? ''
  const result = await downloadRead.run(async signal => {
    await api.downloadCsv(issued.id, activeScope, signal)
    return true
  })
  if (result) exportNotice.value = '已提交 CSV 下载。'
  const error = downloadRead.error.value
  if (error && [403, 404, 409, 410].includes(error.status)) ticketRead.clear()
  invalidateVersion(error)
}

function labelObject(row: ObservationRow) {
  const id = row.site_epoch ?? row.city ?? row.entity
  const chosen = applied.value?.objects.find(item => objectId(item) === id)
  return chosen ? objectName(chosen) : `#${id ?? '—'}`
}
const reasons: Record<string, string> = { no_observation_at_time: '该精确时刻没有观测。', unresolved_dispute: '该精确观测仍有未解决争议。',
  invalid: '该精确观测无效。', suspect: '该精确观测处于可疑状态。', missing: '该精确观测缺测。', censored: '该精确观测为删失记录。' }
const readError = computed(() => exactRead.error.value?.message)
const qualityDescription = computed(() => {
  if (applied.value?.mode === 'detail') return '可披露实到记录，包括零值、删失和缺测。'
  if (applied.value?.mode === 'latest_valid') return '每对象和指标只返回最近的有效观测。'
  return ''
})
</script>

<template>
  <div class="page-content query-page">
    <header class="section-head">
      <div>
        <h1>数据查询</h1>
        <p>按来源、对象和时段查看观测，或确认整组明细范围后申请 CSV。</p>
      </div>
    </header>

    <form class="panel query-panel" aria-labelledby="query-conditions" novalidate @submit.prevent="apply">
      <h2 id="query-conditions">查询条件</h2>
      <div v-if="hasHandoff" class="query-bar">
        <p v-if="handoffError" class="status status-error" role="alert">{{ handoffError }}</p>
        <p v-else-if="object && isWhole" class="form-hint" role="status">已带入分析条件，请核对并应用整组明细查询。</p>
        <p v-else-if="catalogCursor" class="form-hint" role="status">可继续查找获准目录中的原对象。</p>
        <RouterLink class="btn" :to="{ name: 'query', query: scopeQuery(session.scope) }">重新选择查询条件</RouterLink>
      </div>
      <div class="query-grid">
        <label class="field query-object">
          来源产品与对象
          <select v-model="selectedKey" :disabled="!options.length" @change="pickObject">
            <option value="">选择获准目录项</option>
            <option v-for="item in options" :key="objectKey(item)" :value="objectKey(item)">
              {{ item.shape }} · {{ objectName(item) }}{{ item.site_epoch ? ` · 站址期 #${item.site_epoch}` : '' }} · {{ item.source }}
            </option>
          </select>
        </label>
        <div class="query-actions">
          <button v-if="catalogCursor" type="button" class="btn" :disabled="directoryRead.busy.value" @click="readDirectory(catalogCursor)">加载更多目录项</button>
          <button v-if="directoryRead.error.value" type="button" class="btn" @click="readDirectory()">重查对象目录</button>
        </div>
      </div>
      <p v-if="directoryRead.busy.value" class="form-hint" role="status">正在读取获准目录…</p>
      <p v-else-if="directoryRead.error.value" class="status status-error" role="alert">{{ directoryRead.error.value.message }}</p>
      <p v-else-if="!options.length && !hasHandoff" class="status" role="status">当前数据域没有可发现的获准对象。</p>

      <template v-if="object">
        <p class="form-hint query-anchor">
          {{ description }} · 目录锚点 {{ object.observed_at || object.local_report_date || '未知' }}。目录锚点只确认一条获准观测，其他时段覆盖需查询。
        </p>
        <details v-if="sameProduct.length && !daily" class="query-advanced">
          <summary>加入同产品对象{{ extras.length ? `（${extras.length}）` : '' }}</summary>
          <fieldset class="query-set">
            <legend>同类对象合计最多 5 个</legend>
            <div class="query-choice-list">
              <label v-for="item in sameProduct" :key="objectKey(item)" class="query-check">
                <input v-model="extras" type="checkbox" :value="objectKey(item)" :disabled="extras.length === 4 && !extras.includes(objectKey(item))">
                {{ objectName(item) }}{{ item.site_epoch ? ` · 站址期 #${item.site_epoch}` : '' }}
              </label>
            </div>
          </fieldset>
        </details>
        <fieldset v-if="!daily" class="query-set query-modes">
          <legend>查询方式</legend>
          <div class="query-choice-list">
            <label class="query-check"><input v-model="mode" value="window" name="query-mode" type="radio">固定时间窗</label>
            <label class="query-check"><input v-model="mode" value="latest_valid" name="query-mode" type="radio">最近有效</label>
            <label class="query-check"><input v-model="mode" value="detail" name="query-mode" type="radio">整组明细</label>
          </div>
        </fieldset>
        <div class="query-grid">
          <label class="field">
            统计窗口
            <select v-model="windowKind" @change="pickWindow">
              <option v-for="choice in selectionWindows" :key="choice" :value="choice">{{ windows[choice].label }}</option>
            </select>
          </label>
          <label v-if="mode === 'window' && !daily" class="field">
            质量
            <select v-model="quality">
              <option v-for="(label, key) in qualities" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
          <label v-if="daily" class="field">
            源地方报告日
            <input id="query-report-date" v-model="reportDate" type="date">
          </label>
        </div>
        <fieldset class="query-set">
          <legend>指标</legend>
          <div v-if="daily" class="query-choice-list">
            <label v-for="indicator in selectionIndicators" :key="indicator" class="query-check">
              <input type="radio" name="query-daily-indicator" :checked="indicators.includes(indicator)" @change="indicators = [indicator]; pickIndicators()">
              {{ indicator }} <span class="query-muted">{{ unitFor(indicator) }}</span>
            </label>
          </div>
          <div v-else class="query-choice-list">
            <label v-for="indicator in selectionIndicators" :key="indicator" class="query-check">
              <input v-model="indicators" type="checkbox" :value="indicator" @change="pickIndicators">
              {{ indicator }} <span class="query-muted">{{ unitFor(indicator) }}</span>
            </label>
          </div>
        </fieldset>
        <div v-if="!daily" class="query-grid">
          <label v-if="isLatest" class="field">
            截止时刻（可选）
            <input id="query-as-of" v-model="asOf" type="text" autocomplete="off" placeholder="2024-02-01T09:00:00Z" aria-describedby="query-time-hint">
          </label>
          <template v-else>
            <label class="field">
              开始时刻（含）
              <input id="query-start" v-model="start" type="text" autocomplete="off" placeholder="2024-02-01T09:00:00Z" aria-describedby="query-time-hint">
            </label>
            <label class="field">
              结束时刻（不含）
              <input id="query-end" v-model="end" type="text" autocomplete="off" placeholder="2024-02-01T12:00:00Z" aria-describedby="query-time-hint">
            </label>
          </template>
        </div>
        <p v-if="!daily" id="query-time-hint" class="form-hint">使用带时区的 ISO 时刻，如 2024-02-01T09:00:00Z。{{ isLatest ? '留空时使用服务评估时刻。' : '缺失的历史时段保持为空。' }}</p>
        <div class="query-actions">
          <button id="query-time-trigger" ref="timeDialogTrigger" type="button" class="btn" aria-haspopup="dialog" @click="timeDialogOpen = true">设置查询时段</button>
        </div>
        <details class="query-advanced" :open="daily || isWhole ? true : undefined">
          <summary>单位与标准系列</summary>
          <div class="query-grid">
            <label v-if="!daily && !isWhole && selectedUnits.length === 1" class="field">
              单位筛选
              <select v-model="unit"><option value="">不限</option><option :value="selectedUnits[0]">{{ selectedUnits[0] }}</option></select>
            </label>
            <label v-if="needsStandard && (daily || isWhole || indicators.every(indexed))" class="field">
              AQI / 等级标准系列
              <select v-model="standard">
                <option v-if="!daily" value="">{{ isWhole ? '选择标准系列' : '不限标准' }}</option>
                <option value="HJ633-2012">HJ633-2012</option>
                <option value="HJ633-2026">HJ633-2026</option>
                <option value="unknown">未知标准</option>
              </select>
            </label>
            <p v-else class="form-hint">浓度指标的标准系列为不适用。混合指标查询可查看每条记录自己的标准。</p>
          </div>
        </details>
        <p v-if="isWhole" class="form-hint">整组明细固定使用明确时间窗和可靠质量口径，预览范围包括获准的实到质量记录。导出前可单独确认范围和字段。</p>
        <p v-if="formError" role="alert" class="status status-error">{{ formError }}</p>
        <div class="query-actions">
          <button class="btn primary" type="submit"><Search aria-hidden="true" /> {{ daily ? '读取日报' : '应用查询' }}</button>
        </div>
      </template>
    </form>
    <QueryTimeDialog ref="timeDialog" v-model="timeDialogOpen" :mode="daily ? 'daily' : isLatest ? 'latest' : 'range'"
      :choices="selectionWindows" :window="windowKind" :start="start" :end="end" :as-of="asOf" :report-date="reportDate" :opener="timeDialogTrigger" :resume-draft="timeDialogResume"
      @use="useTimeDraft" />

    <section v-if="exactOpen" class="panel query-panel" aria-labelledby="query-exact-title" :aria-busy="exactRead.busy.value">
      <div class="query-bar">
        <h2 id="query-exact-title" ref="exactHeading" tabindex="-1">{{ applied?.mode === 'daily' ? '源地方日报' : '精确观测' }}</h2>
        <button v-if="applied?.mode !== 'daily'" type="button" class="btn" @click="closeExact">关闭精确观测</button>
      </div>
      <p v-if="applied?.mode === 'daily'" class="form-hint">{{ applied?.product }} · {{ applied?.source }} · {{ applied?.condition }}</p>
      <p v-if="exactRead.busy.value" class="status" role="status">正在核对这一条观测…</p>
      <p v-else-if="readError" class="status status-error" role="alert">{{ readError }}</p>
      <QueryResultTable v-else-if="exactResult" :rows="[exactResult]" :object-label="labelObject" table-label="精确读取结果" />
      <p v-else-if="exactRead.data.value" class="status" role="status">{{ reasons[exactRead.data.value.reason || ''] || '该精确选择没有可显示的读数。' }}</p>
      <p v-if="exactResult?.input_version" class="form-hint query-version">精确读版本 <code>{{ exactResult.input_version }}</code></p>
    </section>

    <section v-if="applied && applied.mode !== 'daily'" class="panel query-panel" aria-labelledby="query-result-title" :aria-busy="queryBusy">
      <div class="query-bar">
        <h2 id="query-result-title">{{ applied.mode === 'detail' ? '整组明细预览' : '查询结果' }}</h2>
        <button v-if="applied.mode === 'detail' && resultState === 'ok'" type="button" ref="previewButton" class="btn" :disabled="dirty || queryBusy" @click="reviewExport"><Download aria-hidden="true" /> 确认导出范围</button>
      </div>
      <p class="form-hint query-condition">{{ applied.product }} · {{ applied.source }} · {{ applied.objects.map(objectName).join('、') }} · {{ applied.condition }}</p>
      <p v-if="qualityDescription" class="form-hint">{{ qualityDescription }}</p>
      <p v-if="observationRead.data.value" class="form-hint">
        评估时刻 {{ observationRead.data.value.evaluated_at }}
        <template v-if="observationRead.data.value.selection.as_of"> · 截止 {{ observationRead.data.value.selection.as_of }}</template>
        <template v-else> · 质量 {{ qualities[(applied.query?.quality || 'all') as QueryQuality] }}</template>
      </p>
      <p v-if="detailRead.data.value" class="form-hint query-version">来源时区 {{ detailRead.data.value.source_timezone }} · 整组输入版本 <code>{{ detailVersion }}</code></p>
      <p v-if="dirty" class="status" role="status">条件已编辑。以下仍为上次应用的结果。</p>
      <p v-if="queryBusy" class="status" role="status">正在读取查询结果…</p>
      <template v-else-if="resultError">
        <p class="status status-error" role="alert">{{ resultError }}</p>
        <button type="button" class="btn" :disabled="dirty" @click="restart">从第一页重查</button>
      </template>
      <template v-else-if="resultState === 'ok'">
        <QueryResultTable :rows="rows" :object-label="labelObject" :allow-exact="applied.mode !== 'detail' && !dirty" :shape="applied.product" table-label="本页观测" @inspect="inspect" />
        <div class="query-bar query-paging">
          <p class="form-hint" role="status">第 {{ pageIndex + 1 }} 页 · 本页 {{ rows.length }} 条<span v-if="wholeCount !== null"> · 整组 {{ wholeCount }} 条</span></p>
          <div class="query-actions">
            <button v-if="pageIndex > 0" type="button" class="btn" :disabled="dirty" @click="loadPage(pageIndex - 1, cursors[pageIndex - 1])">上一页</button>
            <button v-if="page?.next_cursor" type="button" class="btn" :disabled="dirty" @click="loadPage(pageIndex + 1, page.next_cursor)">下一页</button>
          </div>
        </div>
      </template>
      <p v-else-if="resultState === 'no_coverage'" class="status" role="status">选择时段超出已核站址期覆盖。</p>
      <p v-else-if="resultState === 'no_data'" class="status" role="status">获准范围内没有符合当前条件的观测。</p>
      <p v-if="exportNotice && !confirmOpen" class="status" role="status">{{ exportNotice }} <button type="button" class="btn" :disabled="dirty" @click="restart">从第一页重查</button></p>
      <div v-if="applied.mode !== 'detail' && resultState && object && !daily" class="query-export-hint">
        <p class="form-hint">自由查询条件与整组 CSV 的口径不同。可打开整组明细，按显式时段重新应用并确认范围。</p>
        <button type="button" class="btn" @click="beginWhole">打开整组明细</button>
      </div>
    </section>

    <section v-if="confirmOpen && detailParent && applied" class="panel query-panel query-confirm" aria-labelledby="query-export-title">
      <h2 id="query-export-title" ref="exportScopeHeading" tabindex="-1">确认整组 CSV</h2>
      <dl class="query-summary">
        <div><dt>数据域与来源</dt><dd>{{ detailParent.selection.scope === 'synthetic' ? '合成演示' : '正式观测' }} · {{ applied.product }} · {{ detailParent.selection.source }}</dd></div>
        <div><dt>所选对象</dt><dd>{{ applied.objects.map(item => `${objectName(item)} (#${objectId(item)})`).join('、') }}</dd></div>
        <div><dt>半开时段</dt><dd>[{{ detailParent.selection.start }}, {{ detailParent.selection.end }}) · {{ detailRead.data.value?.source_timezone }}</dd></div>
        <div><dt>报告与质量</dt><dd>{{ windows[detailParent.selection.window_definition].label }} · reliable，获准实到记录</dd></div>
        <div><dt>指标、单位和标准</dt><dd>{{ detailParent.selection.indicators.map(indicator => `${indicator} · ${detailParent?.selection.units[indicator]} · ${detailParent?.selection.standard_series[indicator]}`).join('；') }}</dd></div>
        <div><dt>输入版本</dt><dd><code>{{ detailVersion }}</code></dd></div>
        <div v-if="detailRead.data.value?.attribution.length"><dt>来源署名</dt><dd>{{ detailRead.data.value.attribution.join('；') }}</dd></div>
      </dl>
      <p>文件覆盖本组的完整范围。{{ wholeCount === null ? '翻至末页可见整组条数。' : `已核整组 ${wholeCount} 条。` }}</p>
      <p class="form-hint">固定字段包括来源署名、输入版本、对象及修订、时标、指标、单位、数值、质量与删失界限。</p>
      <fieldset class="query-set" :disabled="readonlyExport || Boolean(ticket)">
        <legend>可选导出字段（服务逐项核权）</legend>
        <div class="query-choice-list">
          <label v-for="field in csvFields" :key="field" class="query-check">
            <input v-model="fields" :value="field" type="checkbox">{{ csvFieldLabels[field] }}
          </label>
        </div>
      </fieldset>
      <p v-if="exportError" class="status status-error" role="alert">{{ exportError.message }}</p>
      <p v-if="exportNotice" class="status" role="status">{{ exportNotice }}</p>
      <p v-if="ticket" class="form-hint">票据有效至 {{ ticket.expires_at }}。下载时服务重新检查权限和版本。</p>
      <div class="query-actions">
        <button v-if="ticket" type="button" ref="nextDownload" class="btn primary" :disabled="readonlyExport" @click="download(ticket)"><Download aria-hidden="true" /> {{ downloadRead.busy.value ? '正在核对下载权限…' : '下载 CSV' }}</button>
        <button v-else-if="ticketRead.data.value === null" type="button" class="btn primary" :disabled="readonlyExport" @click="prepare">{{ ticketRead.busy.value ? '正在申请票据…' : '确认并申请 CSV 票据' }}</button>
        <button type="button" class="btn" @click="cancelExport">取消导出</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.query-page { min-width: 0; }
.query-panel { display: grid; min-width: 0; gap: 12px; padding: clamp(14px, 2vw, 23px); scroll-margin-top: 80px; }
.query-panel h2, .query-panel p { margin: 0; }
.query-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: end; gap: 12px; }
.query-object { min-width: 0; }
.query-object select, .query-grid select { width: 100%; min-width: 0; min-height: 44px; padding: 8px 11px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: var(--aq-content); color: var(--aq-text); }
.query-grid input { min-height: 44px; }
.query-actions, .query-choice-list, .query-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.query-bar { justify-content: space-between; }
.query-bar h2:focus, .query-confirm h2:focus { scroll-margin-top: 80px; }
.query-actions .btn, .query-bar .btn { min-height: 44px; }
.query-set { min-width: 0; margin: 0; padding: 0; border: 0; }
.query-set legend { padding: 0; color: var(--aq-secondary); font-size: 14px; font-weight: 620; }
.query-check { display: inline-flex; min-height: 42px; align-items: center; gap: 7px; padding: 3px 10px 3px 4px; font-size: 14px; cursor: pointer; }
.query-check input { width: 17px; height: 17px; margin: 0; accent-color: var(--aq-brand); }
.query-advanced { min-width: 0; padding: 0 10px 2px; border: 1px solid var(--aq-border); border-radius: 9px; }
.query-advanced summary { min-height: 42px; padding: 9px 0; color: var(--aq-link); cursor: pointer; }
.query-advanced > .query-grid { margin: 0 0 8px; }
.query-advanced > .query-set { margin: 0 0 8px; }
.query-anchor, .query-condition, .query-summary, .query-version { overflow-wrap: anywhere; }
.query-muted { color: var(--aq-muted); font-size: 12px; }
.query-paging, .query-export-hint { padding-top: 9px; border-top: 1px solid var(--aq-border); }
.query-export-hint { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.query-confirm { border-color: var(--aq-control-border); }
.query-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 18px; margin: 0; }
.query-summary dt { color: var(--aq-secondary); font-size: 13px; }
.query-summary dd { margin: 2px 0 0; }
@media (max-width: 620px) {
  .query-grid, .query-summary { grid-template-columns: minmax(0, 1fr); }
  .query-check { min-height: 44px; }
}
</style>
