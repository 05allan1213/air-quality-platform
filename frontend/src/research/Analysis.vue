<script setup lang="ts">
import { computed, nextTick, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import { useSessionResumeDraft } from '../stores/sessionResumeDraft'
import AnalysisResults from './AnalysisResults.vue'
import {
  canAdd, detailRequest, grains, makeParent, methods, observationValue, parentOf, qualityNames,
  seriesDiscoveryWindow, seriesKey, seriesLabel, stamp, timeWindow, wholeQueryForResult, windowNames,
  type Bounds, type Detail, type Grain, type Method, type Rerun, type Result,
  type Saved, type Series, type SeriesPage, type Support,
} from './analysisContract'

const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const parseMethod = (method: unknown): Method => method === 'compare' || method === 'pearson' ? method : 'statistics'
const method = ref(parseMethod(route.query.method))
const grain = ref<Grain>('day')
const start = ref('')
const end = ref('')
const discoveryFrom = ref('')
const productFilter = ref('')
const indicator = ref('')
const catalog = usePrivateRead<SeriesPage>()
const analysis = usePrivateRead<Result>()
const details = usePrivateRead<Detail>()
const save = usePrivateRead<Saved>()
const rerun = usePrivateRead<Rerun>()
const result = shallowRef<Result | null>(null)
const wholeQuery = computed(() => result.value ? wholeQueryForResult(result.value, session.scope) : null)
const options = shallowRef<Series[]>([])
const chosen = shallowRef<Series[]>([])
const nextCatalog = ref<string | null>(null)
const discovered = shallowRef<{ bounds: Bounds; params: Record<string, unknown> } | null>(null)
const activeDetail = shallowRef<{ request: Support; label: string } | null>(null)
const detailPage = ref(1)
const detailHeading = ref<HTMLElement | null>(null)
const detailTrigger = shallowRef<HTMLElement | null>(null)
const title = ref('')
const saved = shallowRef<Saved | null>(null)
const notice = ref('')
const windowIssue = ref('')
const shareQuery = computed(() => ({ ...scopeQuery(session.scope), ...(method.value === 'statistics' ? {} : { method: method.value }) }))
const windows = computed(() => timeWindow(start.value, end.value, method.value, grain.value))
const longWindow = computed(() => {
  const interval = windows.value
  return interval ? seriesDiscoveryWindow(interval)?.end !== interval.end : false
})
const parent = computed(() => discovered.value ? makeParent(method.value, grain.value, session.scope, discovered.value.bounds, chosen.value) : null)
const catalogGroups = computed(() => {
  const grouped = new Map<string, { choice: Series; intervals: Series[] }>()
  for (const row of options.value) {
    const key = seriesKey(row)
    const group = grouped.get(key) ?? { choice: row, intervals: [] }
    group.intervals.push(row)
    grouped.set(key, group)
  }
  return grouped
})
const supportNames = { observed: '到报记录', effective: '有效样本', paired: '共同有效样本' }
const rerunNotices: Record<Rerun['state'], string> = {
  current: '已按当前权限重跑，数据与保存时一致。',
  data_changed: '已按当前权限重跑，数据版本已变更。',
  baseline_unavailable: '已按当前权限重跑，保存时的数据版本不可比对。',
  target_unavailable: '方案目标当前不可用。',
  method_changed: '方案方法已变更，请重新选择条件。',
}

function closeDetails(restoreFocus = false) {
  details.clear()
  activeDetail.value = null
  detailPage.value = 1
  if (restoreFocus) detailTrigger.value?.focus()
  detailTrigger.value = null
}

function clearResult() {
  analysis.clear()
  save.clear()
  rerun.clear()
  closeDetails()
  result.value = null
  title.value = ''
  saved.value = null
  notice.value = ''
}

function invalidate() {
  clearResult()
  catalog.clear()
  discovered.value = null
  options.value = []
  chosen.value = []
  nextCatalog.value = null
  windowIssue.value = ''
}

function onClearPrivate() {
  invalidate()
  start.value = ''
  end.value = ''
  discoveryFrom.value = ''
  productFilter.value = ''
  indicator.value = ''
}
window.addEventListener('air-quality:clear-reads', onClearPrivate)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', onClearPrivate))

watch(() => route.query.method, value => { method.value = parseMethod(value) })
watch([method, grain, start, end], () => { discoveryFrom.value = '' }, { flush: 'sync' })
watch([method, grain, start, end, discoveryFrom, productFilter, indicator], invalidate, { flush: 'sync' })

function draftDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? value : ''
}

const conditionForm = ref<HTMLFormElement>()
const conditionFields = ['start', 'end', 'product', 'indicator', 'grain', 'discoveryFrom']
const resumed = useSessionResumeDraft(() => {
  const field = document.activeElement
  const focusName = (field instanceof HTMLInputElement || field instanceof HTMLSelectElement)
    && conditionForm.value?.contains(field) && conditionFields.includes(field.name) ? field.name : ''
  return {
    start: draftDate(start.value), end: draftDate(end.value), discoveryFrom: draftDate(discoveryFrom.value),
    product: /^\d{1,16}$/.test(productFilter.value.trim()) ? productFilter.value.trim() : '',
    indicator: indicator.value, grain: grain.value, focusName,
    hadCatalog: discovered.value !== null, selected: chosen.value.map(seriesKey),
  }
})
if (resumed) {
  // The route supplies the method. Restore only form conditions and rediscover authorized series.
  grain.value = resumed.grain
  start.value = resumed.start
  end.value = resumed.end
  discoveryFrom.value = resumed.discoveryFrom
  productFilter.value = resumed.product
  indicator.value = resumed.indicator
  onMounted(() => {
    const control = conditionForm.value?.elements.namedItem(resumed.focusName)
    if (control instanceof HTMLElement) control.focus({ preventScroll: true })
    if (resumed.hadCatalog) void loadCatalog(undefined, resumed.selected)
  })
}

async function chooseMethod(next: Method) {
  if (method.value === next) return
  method.value = next
  await router.replace({ name: 'analysis', query: shareQuery.value })
}

function revoked(error: ApiError | null) {
  return error?.status === 403 && ['use_denied', 'permission_denied', 'not_authenticated'].includes(error.code)
}

function clearOnDenial(error: ApiError | null) {
  if (revoked(error)) {
    invalidate()
    notice.value = error?.message ?? ''
  }
}

async function loadCatalog(cursor?: string, restoreKeys?: readonly string[]) {
  if (!cursor) {
    invalidate()
    const interval = windows.value
    if (!interval) {
      windowIssue.value = '请填写递增的起止时间。一般查询最多 30 天，单对象单指标月度或季节统计最多 366 天。'
      return
    }
    const filter = productFilter.value.trim()
    const product = Number(filter)
    if (filter && (!/^\d+$/.test(filter) || !Number.isSafeInteger(product) || product < 1)) {
      windowIssue.value = '产品编号应为浏览器可精确表示的正整数。'
      return
    }
    const discovery = seriesDiscoveryWindow(interval, longWindow.value ? discoveryFrom.value : '')
    if (!discovery) {
      windowIssue.value = '发现起点须在分析窗口内，且早于结束时间。'
      return
    }
    discovered.value = {
      bounds: interval,
      params: {
        purpose: 'analysis', method: method.value, ...discovery,
        ...scopeQuery(session.scope), ...(filter ? { product } : {}),
        ...(indicator.value ? { indicator: indicator.value } : {}), page_size: 25,
      },
    }
  }
  const active = discovered.value
  if (!active) return
  const response = await catalog.run(signal => api.get<SeriesPage>('catalog/series/', {
    ...active.params, ...(cursor ? { cursor } : {}),
  }, signal))
  if (active !== discovered.value) return
  if (response) {
    options.value = [...options.value, ...response.results]
    nextCatalog.value = response.page.next_cursor
    if (restoreKeys) {
      for (const key of restoreKeys) {
        const row = response.results.find(item => seriesKey(item) === key)
        if (row && canAdd(method.value, chosen.value, row)) chosen.value = [...chosen.value, row]
      }
    }
  } else if (revoked(catalog.error.value) || ['page_changed', 'invalid_cursor'].includes(catalog.error.value?.code ?? '')) {
    const message = catalog.error.value?.message
    invalidate()
    notice.value = message ?? ''
  }
}

function selected(item: Series) {
  return chosen.value.some(row => seriesKey(row) === seriesKey(item))
}

function toggle(item: Series) {
  if (!canAdd(method.value, chosen.value, item)) return
  clearResult()
  chosen.value = selected(item)
    ? chosen.value.filter(row => seriesKey(row) !== seriesKey(item))
    : [...chosen.value, item]
}

async function apply() {
  const request = parent.value
  if (!request) return
  clearResult()
  const endpoint = request.method === 'compare' ? 'analysis/compare/' : 'analysis/statistics/'
  const response = await analysis.run(signal => api.post<Result>(endpoint, request, signal))
  if (response) result.value = response
  else clearOnDenial(analysis.error.value)
}

async function fetchDetails(cursor?: string) {
  const applied = result.value
  const drilldown = activeDetail.value
  if (!applied || !drilldown) return
  const response = await details.run(signal => api.post<Detail>('analysis/details/', detailRequest(applied, drilldown.request, cursor), signal))
  if (response) detailPage.value = cursor ? detailPage.value + 1 : 1
  else if (revoked(details.error.value)) clearOnDenial(details.error.value)
  else if (['input_changed', 'page_changed', 'invalid_cursor'].includes(details.error.value?.code ?? '')) {
    clearResult()
    notice.value = '数据或支撑页已变化。请重新运行分析，再从第一页查看明细。'
  }
}

async function openDetails(request: Support, label: string, event: MouseEvent) {
  const trigger = event.currentTarget as HTMLElement | null
  closeDetails()
  detailTrigger.value = trigger
  activeDetail.value = { request, label }
  const pending = fetchDetails()
  await nextTick()
  detailHeading.value?.focus()
  await pending
}

async function saveIntent() {
  const applied = result.value
  const name = title.value.trim()
  if (!applied || !name || saved.value) return
  const response = await save.run(signal => api.post<Saved>('me/saved-queries/', { ...parentOf(applied), title: name }, signal))
  if (response) saved.value = response
  else clearOnDenial(save.error.value)
}

async function runSaved() {
  const item = saved.value
  if (!item) return
  closeDetails()
  result.value = null
  notice.value = ''
  const suffix = session.scope ? '?scope=synthetic' : ''
  const response = await rerun.run(signal => api.post<Rerun>(`me/saved-queries/${item.id}/run/${suffix}`, {}, signal))
  if (response) {
    result.value = response.result ?? null
    notice.value = rerunNotices[response.state]
  } else clearOnDenial(rerun.error.value)
}
</script>

<template>
  <div class="analysis-page">
    <header class="section-head analysis-heading">
      <div><h1>分析工作台</h1><p>按来源、对象和时间窗口选择分析数据，查看覆盖与支撑记录。</p></div>
      <span class="analysis-domain">{{ session.scope ? 'F 合成验证' : '正式数据' }}</span>
    </header>

    <nav class="analysis-modes" aria-label="分析方法">
      <button v-for="(entry, key) in methods" :key="key" type="button" :class="{ active: key === method }" :aria-pressed="key === method" @click="chooseMethod(key)">{{ entry.label }}</button>
    </nav>

    <section class="panel analysis-section" aria-labelledby="analysis-select-title">
      <div class="analysis-section-heading">
        <div><h2 id="analysis-select-title">查询条件</h2><p>{{ methods[method].instruction }}</p></div>
      </div>
      <form ref="conditionForm" class="analysis-search" @submit.prevent="loadCatalog()">
        <label class="field">起始时间（UTC）<input v-model="start" name="start" type="datetime-local" required /></label>
        <label class="field">结束时间（UTC，不含）<input v-model="end" name="end" type="datetime-local" required /></label>
        <label class="field">产品编号（可选）<input v-model="productFilter" name="product" inputmode="numeric" type="text" /></label>
        <label v-if="method !== 'pearson'" class="field">指标筛选
          <select v-model="indicator" name="indicator"><option value="">全部指标</option><option v-for="item in ['AQI','PM2.5','PM10','SO2','NO2','CO','O3']" :key="item">{{ item }}</option></select>
        </label>
        <label v-if="method !== 'pearson'" class="field">分桶粒度
          <select v-model="grain" name="grain"><option v-for="(label, key) in grains" :key="key" :value="key">{{ label }}</option></select>
        </label>
        <label v-if="longWindow" class="field">系列发现起点（UTC，可选）
          <input v-model="discoveryFrom" name="discoveryFrom" type="datetime-local" aria-describedby="analysis-discovery-hint" />
        </label>
        <button class="btn" :disabled="catalog.busy.value">{{ catalog.busy.value ? '正在发现…' : '发现可选系列' }}</button>
      </form>
      <p v-if="longWindow" id="analysis-discovery-hint">每次最多发现 30 天。默认从分析窗口起点查找，可在无可选系列时改查已有记录的时段。运行分析仍使用上面的完整起止时间。</p>
      <p v-if="windowIssue" class="status status-error" role="alert">{{ windowIssue }}</p>
      <p v-if="catalog.error.value" class="status status-error" role="alert">{{ catalog.error.value.message }}</p>
      <div v-if="discovered" class="analysis-directory" :aria-busy="catalog.busy.value">
        <h3>获准分析的系列</h3>
        <p>许可片段说明可请求的范围。实际到报、覆盖与分析权在运行时核验。</p>
        <p v-if="!options.length && !catalog.busy.value && !catalog.error.value" role="status">当前条件下没有可选系列。请检查时间范围、产品和数据域。</p>
        <div v-if="options.length" class="analysis-table-scroll" tabindex="0" role="region" aria-label="获准分析的系列表">
          <table class="analysis-table">
            <thead><tr><th scope="col">选择</th><th scope="col">对象与指标</th><th scope="col">产品与来源</th><th scope="col">单位与口径</th><th scope="col">获准分析的片段</th></tr></thead>
            <tbody>
              <tr v-for="[key, group] in catalogGroups" :key="key" :class="{ chosen: selected(group.choice) }">
                <td><input :aria-label="`选择 ${seriesLabel(group.choice)} · 产品 #${group.choice.product} · ${windowNames[group.choice.window_definition] ?? group.choice.window_definition} · ${group.choice.standard_series}`" type="checkbox" :checked="selected(group.choice)" :disabled="!canAdd(method, chosen, group.choice)" @change="toggle(group.choice)" /></td>
                <th scope="row">{{ seriesLabel(group.choice) }}</th>
                <td>#{{ group.choice.product }} · {{ group.choice.source }}</td>
                <td>{{ group.choice.unit }} · {{ windowNames[group.choice.window_definition] ?? group.choice.window_definition }} · {{ group.choice.standard_series }}</td>
                <td><div v-for="item in group.intervals" :key="`${item.permitted_from}/${item.permitted_until}`">{{ stamp(item.permitted_from, item.source_timezone) }} → {{ stamp(item.permitted_until, item.source_timezone) }}</div></td>
              </tr>
            </tbody>
          </table>
        </div>
        <button v-if="nextCatalog" type="button" class="btn" :disabled="catalog.busy.value" @click="loadCatalog(nextCatalog)">加载更多系列</button>
        <div v-if="chosen.length" class="analysis-actions">
          <div aria-live="polite"><strong>已选 {{ chosen.length }} 项</strong><p>{{ chosen.map(seriesLabel).join(' → ') }}</p></div>
          <button class="btn primary" type="button" :disabled="!parent || analysis.busy.value" @click="apply">{{ analysis.busy.value ? '正在分析…' : '运行分析' }}</button>
        </div>
        <p v-if="chosen.length && !parent">{{ methods[method].instruction }} 如果已选齐，请缩短时间以符合分析上限。</p>
      </div>
    </section>

    <p v-if="notice" class="status" role="status">{{ notice }}</p>
    <p v-if="analysis.error.value" class="status status-error" role="alert">{{ analysis.error.value.message }}</p>
    <p v-if="rerun.error.value" class="status status-error" role="alert">{{ rerun.error.value.message }}</p>
    <p v-if="rerun.busy.value" class="status" role="status">正在按当前权限重跑…</p>

    <AnalysisResults v-if="result" :result="result" @drill="openDetails" />

    <section v-if="activeDetail && result" class="panel analysis-section" aria-labelledby="analysis-details-title">
      <div class="analysis-section-heading">
        <div><h2 id="analysis-details-title" ref="detailHeading" tabindex="-1">支撑记录</h2><p>{{ activeDetail.label }} · {{ supportNames[activeDetail.request.support] }} · 第 {{ detailPage }} 页</p></div>
        <button type="button" class="btn" @click="closeDetails(true)">关闭支撑记录</button>
      </div>
      <p v-if="details.busy.value" role="status">正在读取同版本记录…</p>
      <p v-else-if="details.error.value" class="status status-error" role="alert">{{ details.error.value.message }}</p>
      <template v-else-if="details.data.value">
        <p v-if="!details.data.value.results.length">该区间没有获准披露的对应记录。</p>
        <div v-else class="analysis-table-scroll" role="region" aria-label="支撑记录表" tabindex="0">
          <table class="analysis-table">
            <thead><tr><th scope="col">观测时间</th><th scope="col">系列</th><th scope="col">数值</th><th scope="col">单位</th><th scope="col">质量</th><th scope="col">贡献批次时间</th></tr></thead>
            <tbody><tr v-for="row in details.data.value.results" :key="`${row.pos}:${row.key}`"><th scope="row">{{ stamp(row.observed_at, result.source_timezone) }}</th><td>{{ result.columns[row.pos]?.indicator }} · #{{ result.columns[row.pos]?.entity }}</td><td>{{ observationValue(row) }}</td><td>{{ row.unit }}</td><td>{{ qualityNames[row.quality] ?? row.quality }}</td><td>{{ stamp(row.fetched_at, result.source_timezone) }}</td></tr></tbody>
          </table>
        </div>
        <div class="analysis-actions"><span>{{ details.data.value.page.next_cursor ? '还有支撑记录' : '已到最后一页' }}</span><div class="analysis-buttons"><button v-if="detailPage > 1" type="button" class="btn" @click="fetchDetails()">返回第一页</button><button v-if="details.data.value.page.next_cursor" type="button" class="btn" @click="fetchDetails(details.data.value.page.next_cursor)">下一页</button></div></div>
      </template>
    </section>

    <section v-if="result || saved" class="panel analysis-section" aria-labelledby="analysis-save-title">
      <h2 id="analysis-save-title">保存分析方案</h2>
      <p>保存已应用的条件，可在我的空间按当前数据和权限重跑。</p>
      <form v-if="!saved" class="analysis-save" @submit.prevent="saveIntent">
        <label class="field">方案名称<input v-model="title" maxlength="100" required /></label>
        <button class="btn" :disabled="save.busy.value || !title.trim()">{{ save.busy.value ? '正在保存…' : '保存方案' }}</button>
      </form>
      <div v-else class="analysis-actions"><span role="status">已保存「{{ saved.title }}」</span><div class="analysis-buttons"><button class="btn" type="button" :disabled="rerun.busy.value" @click="runSaved">重跑已保存方案</button><RouterLink class="btn" :to="{ name: 'me', query: scopeQuery(session.scope) }">打开我的空间</RouterLink></div></div>
      <p v-if="save.error.value" class="status status-error" role="alert">{{ save.error.value.message }}</p>
    </section>

    <section v-if="result?.state === 'ok' && (wholeQuery || result.method !== 'statistics')" class="panel analysis-section" aria-labelledby="analysis-whole-title">
      <h2 id="analysis-whole-title">整组明细与导出</h2>
      <p v-if="wholeQuery">带入已应用的对象、指标和时段，在数据查询中重新读取整组实到记录并确认 CSV 范围。</p>
      <p v-else>对比和关联的支撑记录用于核查研究样本。需要整组 CSV 时，请在数据查询中另选获准范围。</p>
      <div v-if="wholeQuery"><RouterLink class="btn" :to="{ name: 'query', query: wholeQuery }">打开整组明细</RouterLink></div>
    </section>
  </div>
</template>

<style>
.analysis-page { display: grid; min-width: 0; gap: 14px; }
.analysis-heading { align-items: start; }
.analysis-heading h1 { margin: 0; }
.analysis-heading p { margin-top: 6px; }
.analysis-domain { color: var(--aq-secondary); font-size: .875rem; white-space: nowrap; }
.analysis-modes { display: flex; gap: 6px; border-bottom: 1px solid var(--aq-border); }
.analysis-modes button { padding: 10px 14px; min-height: 44px; border: 0; border-bottom: 2px solid transparent; border-radius: 6px 6px 0 0; background: transparent; color: var(--aq-secondary); cursor: pointer; }
.analysis-modes button:hover { background: var(--aq-content); }
.analysis-modes button.active { border-bottom-color: var(--aq-brand); background: var(--aq-content); color: var(--aq-link); font-weight: 650; }
.analysis-section { min-width: 0; padding: 18px 20px; }
.analysis-section h2, .analysis-section h3 { margin: 0; }
.analysis-section h2 { font-size: 1.18rem; }
.analysis-section h3 { font-size: 1rem; }
.analysis-section p { margin: 6px 0 10px; }
.analysis-section-heading, .analysis-actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.analysis-section-heading > div, .analysis-actions > div { min-width: 0; }
.analysis-buttons { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.analysis-search { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 172px), 1fr)); gap: 10px 12px; align-items: end; }
.analysis-search .field { min-width: 0; }
.analysis-search select { width: 100%; min-height: 42px; padding: 8px 10px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: var(--aq-content); color: var(--aq-text); }
.analysis-directory { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; justify-items: start; gap: 8px; border-top: 1px solid var(--aq-border); margin-top: 18px; padding-top: 16px; }
.analysis-directory > * { max-width: 100%; }
.analysis-directory p { margin: 2px 0 6px; }
.analysis-actions { width: 100%; margin-top: 12px; }
.analysis-actions strong, .analysis-actions span { overflow-wrap: anywhere; }
.analysis-table-scroll { width: 100%; min-width: 0; max-height: 30rem; overflow: auto; border: 1px solid var(--aq-border); border-radius: 8px; background: var(--aq-content); }
.analysis-table-scroll:focus-visible, .analysis-disclosure summary:focus-visible { outline: 2px solid var(--aq-focus); outline-offset: 2px; }
.analysis-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: .875rem; }
.analysis-table thead th { position: sticky; top: 0; z-index: 1; background: var(--aq-content-strong); white-space: nowrap; }
.analysis-table th, .analysis-table td { padding: 9px 11px; vertical-align: top; text-align: left; border-bottom: 1px solid var(--aq-border); }
.analysis-table tbody th { font-weight: 600; }
.analysis-table tbody tr:last-child > * { border-bottom: 0; }
.analysis-table tr.chosen { background: var(--aq-content-strong); }
.analysis-table input[type='checkbox'] { width: 24px; height: 24px; margin: 0; accent-color: var(--aq-brand); }
.analysis-table .btn { min-height: 32px; white-space: nowrap; }
.analysis-save { display: flex; align-items: end; gap: 10px; flex-wrap: wrap; }
.analysis-save label { width: min(100%, 24rem); }
@media (max-width: 600px) {
  .analysis-heading { align-items: start; flex-direction: column; gap: 8px; }
  .analysis-section { padding: 14px 12px; }
  .analysis-modes button { padding-inline: 8px; font-size: .85rem; }
  .analysis-page .btn { min-height: 44px; }
}
</style>
