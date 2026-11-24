<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElDialog } from 'element-plus'
import { ArrowDown, ArrowLeft, ArrowRight, DataAnalysis, Document, Download, Operation, Plus, Minus, RefreshLeft, TrendCharts, Warning } from '@element-plus/icons-vue'
import ChartView from '../shared/ChartView.vue'
import { usePreviewStore } from '../shared/preview'
import {
  defaultConditions, detailsFor, downloadExport, indicators, listPlans, modeNames, MockError, objectName, objects,
  productCode, productName, reportFor, requestExport, runSaved, saveAnalysis, scatterPoints, scenarios, unit, windows,
  type Conditions, type DetailRow, type Drilldown, type ExportTicket, type Method, type Quality, type ResearchReport, type Scenario, type Support,
} from './researchMock'
import { correlationChart, distributionChart, lineChart, localTime, number, percent, styleFor, timeSeries } from './chartOptions'

const route = useRoute()
const preview = usePreviewStore()
const modeEntries = Object.entries(modeNames) as [Method, string][]
const modes = { statistics: TrendCharts, compare: DataAnalysis, pearson: Operation }
const supportNames: Record<Support, string> = { observed: '已收到记录', effective: '有效样本', paired: '共同有效时槽' }
const qualityNames: Record<Quality, string> = { valid: '有效', gap: '无记录', missing: '来源缺测', censored: '低于检出限' }

function initialConditions(): Conditions {
  const method = typeof route.query.mode === 'string' && Object.hasOwn(modeNames, route.query.mode) ? route.query.mode as Method : 'compare'
  const conditions = defaultConditions(method, route.query.from === 'city')
  if (typeof route.query.window === 'string' && Object.hasOwn(windows, route.query.window)) conditions.window = route.query.window as Conditions['window']
  return conditions
}

const applied = ref(initialConditions())
const draft = ref(initialConditions())
const report = ref<ResearchReport>(reportFor(applied.value))
const scenario = ref<Scenario>('normal')
const busy = ref(false)
const formError = ref('')
const formStatus = ref('')
const hidden = ref<number[]>([])
const zoom = ref({ start: 0, end: 100 })
const mobileEditing = ref(false)
const narrow = ref(false)
const resultsHeading = ref<HTMLElement>()
let narrowMedia: MediaQueryList | undefined
function updateViewport() { narrow.value = narrowMedia?.matches ?? false }
onMounted(() => {
  narrowMedia = window.matchMedia?.('(max-width: 600px)')
  updateViewport()
  narrowMedia?.addEventListener('change', updateViewport)
})
let requestNumber = 0
let pendingTimer: ReturnType<typeof setTimeout> | undefined

function copy(conditions: Conditions): Conditions {
  return { ...conditions, objectIds: [...conditions.objectIds] }
}
function filterKey(conditions: Conditions) {
  const { method, product, objectIds, indicator, other, window, grain } = conditions
  return JSON.stringify([method, product, [...objectIds].sort((a, b) => a - b), indicator, method === 'pearson' ? other : null, window, grain])
}
const isDraft = computed(() => filterKey(applied.value) !== filterKey(draft.value))
const activeObjects = computed(() => objects[draft.value.product])
const selectedObject = computed({
  get: () => draft.value.objectIds[0],
  set: (id) => { draft.value.objectIds = [id] },
})
const axes = computed(() => report.value.selection.indicators)
const title = computed(() => {
  if (report.value.method === 'pearson') return `${axes.value.join(' 与 ')} 散点`
  if (report.value.method === 'compare') return `${axes.value[0]} 对齐时序`
  return `${axes.value[0]} 浓度趋势`
})
const compare = computed(() => report.value.method === 'compare')
const pearson = computed(() => report.value.method === 'pearson')
const isPaired = computed(() => compare.value || pearson.value)
const supported = computed(() => report.value.conclusion.state === 'sufficient')
const lines = computed(() => isPaired.value && pearson.value ? [] : timeSeries(report.value))
const visibleLines = computed(() => lines.value.filter((item) => !hidden.value.includes(item.entity)))
const lineOption = computed(() => lineChart(report.value, visibleLines.value, zoom.value))
const distributionOption = computed(() => distributionChart(report.value))
const correlationOption = computed(() => correlationChart(report.value))
const isZoomed = computed(() => zoom.value.start > 0 || zoom.value.end < 100)
const canZoom = computed(() => !pearson.value && report.value.grain === 'hour' && (lines.value[0]?.data.length ?? 0) > 8)
const resultDate = computed(() => windows[applied.value.window].date)
const resultSubjects = computed(() => [...new Set(report.value.columns.map((column) => objectName(column.entity)))].join('、'))
const resultGrain = computed(() => report.value.grain === 'day' ? '按地方日' : '按小时')
const resultScopeText = computed(() => {
  if (report.value.conclusion.state === 'insufficient_coverage') return isPaired.value
    ? '共同有效时槽低于 75%。可复核对齐序列与共同样本，本窗口无整期结论。'
    : '有效样本覆盖率低于 75%。可查看现有样本的时序与分布。'
  if (report.value.conclusion.state === 'insufficient_pairs') return '有效配对少于 30 对。散点仍可查看，本窗口不返回相关系数。'
  if (report.value.conclusion.state === 'zero_variance') return '至少一项指标没有方差，本窗口不返回相关系数。'
  if (pearson.value) return '相关系数只描述同一站址期或城市的配对浓度，可能受季节性、自相关和缺测影响。'
  if (compare.value) return '比较结果仅适用于所选合成窗口内的共同有效样本。'
  return '按有效小时样本统计，分布与平均值沿用同一研究窗口。'
})

function clearChartState() {
  hidden.value = []
  zoom.value = { start: 0, end: 100 }
}
function chooseMode(method: Method) {
  if (draft.value.method === method) return
  draft.value = {
    ...draft.value, method,
    objectIds: method === 'compare' ? activeObjects.value.slice(0, 2).map(({ id }) => id) : [draft.value.objectIds[0] ?? activeObjects.value[0].id],
    ...(method === 'pearson' ? { window: 'extended' as const, grain: 'hour' as const } : {}),
  }
  formError.value = ''
  mobileEditing.value = true
}
function closeObjectMenu(event: Event) {
  const details = (event.currentTarget as HTMLElement).closest('details')
  if (details) {
    details.open = false
    details.querySelector('summary')?.focus()
  }
}
function changeProduct() {
  draft.value.objectIds = activeObjects.value.slice(0, draft.value.method === 'compare' ? 2 : 1).map(({ id }) => id)
}
function resetDraft() {
  draft.value = copy(applied.value)
  formError.value = ''
}
function errorText(error: unknown): string {
  if (error instanceof MockError) {
    const messages = {
      invalid_request: '请为对象对比选择至少两个对象。关联分析须选择两项不同的浓度指标。',
      incompatible_series: '所选对象或指标口径不兼容，请核对数据产品。',
      query_too_large: '查询超出范围，请缩小窗口或减少对象。',
      input_changed: '研究输入已变更。请重新开始分析，再查看明细或申请下载。',
      use_denied: '当前来源未授予本次导出权限。仍可继续查看获准的分析与明细。',
      not_found: '该方案或票据在当前演示账号下不可用。',
      expired: '下载票据已过期，请重新申请。',
      database_unavailable: '分析服务暂不可用。请在高级条件中恢复标准样本后重试。',
    }
    return messages[error.code]
  }
  return '原型暂时无法完成此操作，请重试。'
}
function apply(conditions = draft.value) {
  const snapshot = copy(conditions)
  const currentScenario = scenario.value
  const request = ++requestNumber
  clearTimeout(pendingTimer)
  busy.value = true
  formError.value = ''
  formStatus.value = '正在读取合成研究结果…'
  pendingTimer = setTimeout(() => {
    try {
      const next = reportFor(snapshot, currentScenario)
      if (request !== requestNumber) return
      report.value = next
      applied.value = snapshot
      clearChartState()
      ticket.value = null
      formStatus.value = '已应用研究条件。'
      if (narrow.value && !isDraft.value) {
        mobileEditing.value = false
        void nextTick(() => resultsHeading.value?.focus({ preventScroll: true }))
      }
    } catch (error) {
      if (request !== requestNumber) return
      formError.value = errorText(error)
      formStatus.value = ''
    } finally {
      if (request === requestNumber) busy.value = false
    }
  }, 470)
}
function toggleSeries(id: number) {
  if (hidden.value.includes(id)) hidden.value = hidden.value.filter((entry) => entry !== id)
  else if (visibleLines.value.length > 1) hidden.value = [...hidden.value, id]
}
function adjustZoom(zoomIn: boolean) {
  const { start, end } = zoom.value
  const nextSpan = zoomIn ? Math.max(22, (end - start) * 0.67) : Math.min(100, (end - start) * 1.5)
  const low = Math.max(0, Math.min(100 - nextSpan, (start + end - nextSpan) / 2))
  zoom.value = { start: low, end: low + nextSpan }
}

const detailsOpen = ref(false)
const detailFilter = ref<Drilldown>({ support: 'observed' })
const detailRows = ref<DetailRow[]>([])
const detailError = ref('')
const detailPage = ref(1)
const cursors = ref<(string | undefined)[]>([undefined])
const nextCursor = ref<string | null>(null)
function loadDetails(cursor?: string) {
  try {
    const response = detailsFor(report.value, { ...detailFilter.value, input_version: report.value.input_version, page_size: 12, cursor }, scenario.value)
    detailRows.value = response.results
    nextCursor.value = response.page.next_cursor
    detailError.value = ''
  } catch (error) {
    detailRows.value = []
    detailError.value = errorText(error)
    nextCursor.value = null
  }
}
function openDetails(filter: Drilldown = { support: isPaired.value ? 'paired' : 'observed' }) {
  detailFilter.value = filter
  detailPage.value = 1
  cursors.value = [undefined]
  loadDetails()
  detailsOpen.value = true
}
function setDetailSupport(support: Support) {
  const bucket = detailFilter.value.bucket
  openDetails({ support, bucket: bucket ? { start: bucket.start, end: bucket.end, ...(support !== 'paired' && bucket.pos !== undefined ? { pos: bucket.pos } : {}) } : undefined })
}
function navigateDetails(direction: -1 | 1) {
  if (direction === 1 && nextCursor.value) cursors.value[detailPage.value] = nextCursor.value
  const target = detailPage.value + direction
  loadDetails(cursors.value[target - 1])
  if (!detailError.value) detailPage.value = target
}
function pointDetails(point: { seriesIndex: number; dataIndex: number }) {
  if (pearson.value) {
    const selected = scatterPoints(report.value)[point.dataIndex]
    if (selected) openDetails({ support: 'paired', bucket: { start: selected.at, end: new Date(Date.parse(selected.at) + 3_600_000).toISOString() } })
  } else {
    const line = visibleLines.value[point.seriesIndex]
    const bounds = line?.data[point.dataIndex]
    if (line && bounds) openDetails({ support: 'effective', bucket: { start: bounds.start, end: bounds.end, pos: line.pos } })
  }
}
function describeReading(row: DetailRow) {
  if (row.quality === 'censored' && row.censor_limit !== null) return `<${number(row.censor_limit)} ${row.unit}`
  return row.value === null ? '—' : `${number(row.value)} ${row.unit}`
}

const saveOpen = ref(false)
const planTitle = ref('')
const saveError = ref('')
const plans = ref(listPlans(preview.role))
function openPlans() {
  plans.value = listPlans(preview.role)
  planTitle.value = `${modeNames[report.value.method]} · ${axes.value.join(' / ')}`
  saveError.value = ''
  saveOpen.value = true
}
function submitPlan() {
  if (!planTitle.value.trim()) {
    saveError.value = '请输入方案名称。'
    return
  }
  try {
    const saved = saveAnalysis(preview.role, planTitle.value, applied.value)
    plans.value = listPlans(preview.role)
    saveOpen.value = false
    preview.notify(`已保存合成方案「${saved.title}」`)
  } catch (error) { saveError.value = errorText(error) }
}
function rerunPlan(id: string) {
  try {
    draft.value = runSaved(preview.role, id)
    saveOpen.value = false
    apply()
  } catch (error) { saveError.value = errorText(error) }
}

const exportOpen = ref(false)
const ticket = ref<ExportTicket | null>(null)
const exportOwner = ref(preview.role)
const exportError = ref('')
function openExport() {
  exportError.value = ''
  exportOpen.value = true
  if (exportOwner.value !== preview.role) ticket.value = null
}
function prepareExport() {
  exportError.value = ''
  try {
    ticket.value = requestExport(preview.role, report.value, scenario.value)
    exportOwner.value = preview.role
    if (!ticket.value) exportError.value = '当前条件下没有可导出的明细。'
  } catch (error) { exportError.value = errorText(error) }
}
function download() {
  if (!ticket.value) return
  exportError.value = ''
  try {
    const csv = downloadExport(preview.role, ticket.value.id, scenario.value)
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `F-合成样本-${resultDate.value.slice(0, 10)}.csv`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    exportOpen.value = false
    preview.notify('已下载 F 合成样本明细')
  } catch (error) { exportError.value = errorText(error) }
}

watch(() => [route.query.from, route.query.mode, route.query.window], () => {
  clearTimeout(pendingTimer)
  requestNumber++
  busy.value = false
  scenario.value = 'normal'
  mobileEditing.value = false
  formError.value = ''
  formStatus.value = ''
  draft.value = initialConditions()
  applied.value = copy(draft.value)
  report.value = reportFor(applied.value)
  ticket.value = null
  clearChartState()
})
onBeforeUnmount(() => {
  narrowMedia?.removeEventListener('change', updateViewport)
  clearTimeout(pendingTimer)
  requestNumber++
})
</script>

<template>
  <div class="analysis-page">
    <header class="page-title">
      <div class="title-copy">
        <h1>分析工作台</h1>
        <p>探索污染物变化、比较同层对象，并复核结论使用的观测样本。</p>
      </div>
      <div class="actions">
        <button class="btn" type="button" @click="openPlans"><Document aria-hidden="true" /> 保存方案</button>
        <button class="btn" type="button" @click="openExport"><Download aria-hidden="true" /> 导出明细</button>
      </div>
    </header>

    <section class="panel controls" aria-label="研究条件">
      <div class="modebar" role="group" aria-label="分析模式">
        <button v-for="[mode, label] in modeEntries" :key="mode" :aria-pressed="draft.method === mode" type="button" @click="chooseMode(mode)">
          <component :is="modes[mode]" aria-hidden="true" />{{ label }}
        </button>
      </div>
      <div class="mobile-filter-bar">
        <div>
          <span v-if="isDraft" class="tag amber">条件未应用</span>
          <p>已应用 · {{ productCode(report.selection) }} · {{ resultSubjects }} · {{ axes.join(' / ') }}</p>
        </div>
        <button type="button" class="link-btn" :aria-expanded="mobileEditing" aria-controls="research-filters" @click="mobileEditing = !mobileEditing">
          {{ mobileEditing ? '收起条件' : '编辑条件' }}<ArrowDown aria-hidden="true" />
        </button>
      </div>
      <form id="research-filters" class="filter-form" :class="{ 'editing-on-mobile': mobileEditing }" @submit.prevent="apply()">
        <div class="filter-grid" :class="{ 'with-two-indicators': draft.method === 'pearson' }">
          <div class="filter">
            <label for="research-product">数据产品</label>
            <select id="research-product" v-model="draft.product" @change="changeProduct">
              <option value="S">S · 站点小时观测</option>
              <option value="C">C · 城市小时观测</option>
            </select>
          </div>
          <div class="filter">
            <template v-if="draft.method === 'compare'">
              <span id="research-objects-label" class="field-name">分析对象</span>
              <details class="object-choice" @keydown.esc.prevent="closeObjectMenu">
                <summary aria-labelledby="research-objects-label research-object-label"><span id="research-object-label">{{ draft.objectIds.length ? [...draft.objectIds].sort((a, b) => a - b).map(objectName).join('、') : '选择 2–5 个对象' }}</span><ArrowDown aria-hidden="true" /></summary>
                <fieldset class="object-menu">
                  <legend>选择 2–5 个兼容对象</legend>
                  <label v-for="item in activeObjects" :key="item.id" class="object-option">
                    <input v-model="draft.objectIds" type="checkbox" :value="item.id" />{{ item.label }}
                  </label>
                  <button type="button" class="link-btn" @click="closeObjectMenu">完成选择</button>
                </fieldset>
              </details>
            </template>
            <template v-else>
              <label for="research-object">分析对象</label>
              <select id="research-object" v-model="selectedObject">
                <option v-for="item in activeObjects" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
            </template>
          </div>
          <div class="filter">
            <label for="research-indicator">{{ draft.method === 'pearson' ? 'X 轴指标' : '指标' }}</label>
            <select id="research-indicator" v-model="draft.indicator">
              <option v-for="indicator in indicators" :key="indicator" :value="indicator">{{ indicator }} · {{ unit(indicator) }}</option>
            </select>
          </div>
          <div v-if="draft.method === 'pearson'" class="filter">
            <label for="research-other">Y 轴指标</label>
            <select id="research-other" v-model="draft.other">
              <option v-for="indicator in indicators" :key="indicator" :value="indicator">{{ indicator }} · {{ unit(indicator) }}</option>
            </select>
          </div>
          <div class="filter">
            <label for="research-window">时间范围 <small>UTC+8</small></label>
            <select id="research-window" v-model="draft.window" aria-describedby="research-window-help">
              <option v-for="(item, key) in windows" :key="key" :value="key">{{ item.label }}</option>
            </select>
          </div>
          <div class="filter">
            <label for="research-grain">统计粒度</label>
            <select id="research-grain" v-model="draft.grain" :disabled="draft.method === 'pearson'">
              <option value="hour">按小时</option>
              <option value="day">按地方日</option>
            </select>
          </div>
          <button type="submit" class="btn primary apply" :disabled="busy">{{ busy ? '分析中…' : '开始分析' }}<ArrowRight aria-hidden="true" /></button>
        </div>
        <div class="filter-foot">
          <details class="advanced">
            <summary><Operation aria-hidden="true" />高级条件<ArrowDown aria-hidden="true" /></summary>
            <div class="advanced-content">
              <dl>
                <div><dt>产品范围</dt><dd>F 合成演示 · {{ draft.product }} 口径</dd></div>
                <div><dt>质量筛选</dt><dd>可靠记录参与统计</dd></div>
                <div><dt>研究方法版本</dt><dd class="mono">s05.v1</dd></div>
              </dl>
              <div class="filter">
                <label for="research-scenario">响应演练</label>
                <select id="research-scenario" v-model="scenario">
                  <option v-for="(label, key) in scenarios" :key="key" :value="key">{{ label }}</option>
                </select>
              </div>
            </div>
          </details>
          <div v-if="isDraft" class="draft-state"><span class="tag amber">条件未应用</span><button class="link-btn" type="button" @click="resetDraft">还原</button></div>
          <p v-else id="research-window-help" class="muted filter-note">窗口按源地方时间起止，末端不计入。</p>
          <span v-if="isDraft" id="research-window-help" class="sr-only">窗口按源地方时间起止，末端不计入。</span>
        </div>
        <p class="sr-only" role="status">{{ formStatus }}</p>
        <div v-if="formError" class="research-error" role="alert"><Warning aria-hidden="true" />{{ formError }} 当前结果保留上次已应用条件。</div>
      </form>
    </section>

    <section class="research-results" :aria-busy="busy" aria-labelledby="research-result-heading">
      <header class="applied-strip">
        <h2 id="research-result-heading" ref="resultsHeading" tabindex="-1">分析结果</h2>
        <div class="applied-text">
          <span class="tag blue">已应用</span>
          <span>{{ modeNames[report.method] }}</span>
          <span class="applied-objects">{{ productCode(report.selection) }} · {{ resultSubjects }}</span>
          <span>{{ resultDate }} · {{ resultGrain }}</span>
        </div>
      </header>
      <div v-if="report.state === 'no_data'" class="panel research-empty">
        <DataAnalysis aria-hidden="true" />
        <h3>当前窗口没有观测</h3>
        <p>所选对象在 {{ resultDate }} 没有记录。可返回研究条件，选择其他时间范围。</p>
      </div>
      <div v-else class="research-main">
        <section class="panel research-visual" aria-labelledby="analysis-chart-title">
          <header class="panel-head research-chart-head">
            <div>
              <h3 id="analysis-chart-title">{{ title }}</h3>
              <p v-if="pearson">{{ resultSubjects }} · 同一观测时刻的两项浓度</p>
              <p v-else>{{ unit(axes[0]) }} · {{ resultGrain }} · UTC+8</p>
            </div>
            <button class="btn detail-action" type="button" @click="openDetails()"><Document aria-hidden="true" /> 支撑明细</button>
          </header>
          <div class="panel-body research-body">
            <div v-if="!pearson" class="chart-controls">
              <div class="series-legend" role="group" aria-label="时序图例">
                <button v-for="line in lines" :key="line.entity" type="button" class="legend-item" :aria-pressed="!hidden.includes(line.entity)"
                  :disabled="lines.length === 1" @click="toggleSeries(line.entity)">
                  <span class="series-rule" aria-hidden="true" :style="{ borderColor: styleFor(line.entity).color, borderTopStyle: styleFor(line.entity).kind }" />{{ line.name }}
                </button>
              </div>
              <div v-if="canZoom" class="zoom-controls" role="group" aria-label="时序缩放">
                <button class="small-tool" type="button" aria-label="放大图表" title="放大图表" :disabled="zoom.end - zoom.start <= 22" @click="adjustZoom(true)"><Plus aria-hidden="true" /></button>
                <button class="small-tool" type="button" aria-label="缩小图表" title="缩小图表" :disabled="!isZoomed" @click="adjustZoom(false)"><Minus aria-hidden="true" /></button>
                <button class="small-tool" type="button" aria-label="重置缩放" title="重置缩放" :disabled="!isZoomed" @click="zoom = { start: 0, end: 100 }"><RefreshLeft aria-hidden="true" /></button>
              </div>
            </div>
            <div v-else class="scatter-axis muted">Y 轴 · {{ axes[1] }}（{{ unit(axes[1]) }}）</div>
            <ChartView v-if="pearson" :option="correlationOption" :label="`${title}。${report.P} 个共同有效时槽。可使用支撑明细读取数值。`" :height="narrow ? 227 : 263" @point="pointDetails" />
            <ChartView v-else :option="lineOption" :label="`${title}。${resultSubjects}；缺测处断线。可使用支撑明细读取数值。`" :height="narrow ? 227 : 263" @point="pointDetails" />
            <p class="chart-note" v-if="pearson">每个点对应一个共同有效时槽。可选点查看两项记录。</p>
            <p class="chart-note" v-else-if="report.grain === 'day'">按源地方日聚合，覆盖不足的日时槽留空。可选点查看日内样本。</p>
            <p class="chart-note" v-else>缺测处断线。点击观测点可查看该时槽的支撑记录。</p>
          </div>
        </section>

        <aside class="panel research-evidence" aria-labelledby="research-evidence-heading" data-testid="research-coverage">
          <header class="panel-head">
            <div><h3 id="research-evidence-heading">样本与结论</h3><p>{{ isPaired ? '同一窗口的配对覆盖' : '有效样本覆盖' }}</p></div>
          </header>
          <div class="panel-body">
            <div class="coverage-feature">
              <div><strong>{{ percent(isPaired ? report.paired_ratio ?? 0 : report.columns[0].valid_ratio) }}</strong><span>{{ isPaired ? '共同覆盖率' : '有效覆盖率' }}</span></div>
              <span class="tag" :class="supported ? 'green' : 'amber'">{{ supported ? '达到探索门槛' : '样本不足' }}</span>
            </div>
            <div class="coverage-track" aria-hidden="true"><span :class="{ short: !supported }" :style="{ width: percent(isPaired ? report.paired_ratio ?? 0 : report.columns[0].valid_ratio) }" /></div>
            <dl class="coverage-pairs">
              <div v-if="isPaired"><dt>共同有效 / 共同应有（P / E共）</dt><dd>{{ report.P }} / {{ report.E_common }}</dd></div>
              <div v-for="column in report.columns" :key="column.pos">
                <dt>{{ pearson ? column.indicator : objectName(column.entity) }}<span>有效 / 应有</span></dt><dd>{{ column.V }} / {{ column.E }}</dd>
              </div>
            </dl>
            <div class="scope-note" :class="{ insufficient: !supported }">
              <div v-if="pearson && report.pearson != null" class="pearson-result">Pearson <b>r = {{ number(report.pearson, 3) }}</b></div>
              <strong v-else-if="!supported">{{ compare ? '不提供整期排名' : '结论受限' }}</strong>
              <p>{{ resultScopeText }}</p>
            </div>
            <details class="coverage-terms">
              <summary>查看统计口径</summary>
              <p>E 为应有时槽，O 为收到的记录，V 为有效记录。E共 是各序列应有时槽的交集，P 是其中全部有效的时槽。</p>
              <p>探索门槛为覆盖率 75%。关联还需至少 30 对样本且两项指标均有方差。</p>
              <ul>
                <li v-for="column in report.columns" :key="column.pos">{{ pearson ? column.indicator : objectName(column.entity) }} · O {{ column.O }}，未收到 {{ column.exclusions.gap }}，缺测 {{ column.exclusions.missing }}，低于检出限 {{ column.exclusions.censored }}</li>
              </ul>
            </details>
          </div>
        </aside>
      </div>

      <section v-if="report.state === 'ok' && report.distribution" class="panel secondary-result">
        <header class="panel-head"><div><h3>浓度分布</h3><p>{{ resultSubjects }} · {{ axes[0] }}（{{ unit(axes[0]) }}） · {{ report.columns[0].V }} 个有效小时</p></div></header>
        <div class="panel-body">
          <ChartView :option="distributionOption" :label="`${axes[0]} 浓度分布，${report.columns[0].V} 个有效小时。区间计数可在下方读取。`" :height="165" />
          <details class="coverage-terms">
            <summary>查看分布计数</summary>
            <dl class="bin-counts">
              <div v-for="(bin, index) in report.distribution" :key="index">
                <dt>{{ bin.lower === null ? `小于 ${bin.upper}` : bin.upper === null ? `${bin.lower} 及以上` : `${bin.lower} 至小于 ${bin.upper}` }}</dt>
                <dd>{{ bin.n }} 小时</dd>
              </div>
            </dl>
          </details>
        </div>
      </section>
      <section v-if="report.state === 'ok' && report.ranking?.length" class="panel secondary-result ranking-result">
        <header class="panel-head"><div><h3>共同样本均值排序</h3><p>{{ axes[0] }} · 根据 {{ report.P }} 个共同有效时槽计算</p></div></header>
        <div class="panel-body">
          <table>
            <thead><tr><th scope="col">排序</th><th scope="col">对象</th><th scope="col">均值（{{ unit(axes[0]) }}）</th></tr></thead>
            <tbody><tr v-for="rank in report.ranking" :key="rank.entity"><td>{{ rank.rank }}</td><td>{{ objectName(rank.entity) }}</td><td>{{ number(rank.mean) }}</td></tr></tbody>
          </table>
        </div>
      </section>
    </section>

    <ElDialog v-model="detailsOpen" title="支撑明细" width="850px" append-to-body>
      <p class="dialog-intro">F · {{ resultSubjects }} · {{ resultDate }} · UTC+8。明细沿用已应用条件与研究输入版本。</p>
      <p v-if="detailFilter.bucket" class="detail-selection">下钻范围 {{ localTime(detailFilter.bucket.start) }} 至 {{ localTime(detailFilter.bucket.end) }}<button type="button" class="link-btn" @click="openDetails({ support: detailFilter.support })">查看整个窗口</button></p>
      <div class="support-switch" role="group" aria-label="明细支持范围">
        <button v-for="(name, key) in supportNames" v-show="key !== 'paired' || isPaired" :key="key" type="button" class="btn" :class="{ primary: key === detailFilter.support }" :aria-pressed="key === detailFilter.support" @click="setDetailSupport(key)">{{ name }}</button>
      </div>
      <div v-if="detailError" class="research-error inside-dialog" role="alert">{{ detailError }}</div>
      <template v-else>
        <div v-if="detailRows.length" class="table-wrap details-table" tabindex="0" role="region" aria-label="支撑明细数据表，可横向滚动">
          <table>
            <thead><tr><th scope="col">观测时间（UTC+8）</th><th scope="col">对象</th><th scope="col">指标</th><th scope="col">值</th><th scope="col">质量</th><th scope="col">修订</th></tr></thead>
            <tbody><tr v-for="row in detailRows" :key="row.key"><td>{{ localTime(row.observed_at) }}</td><td>{{ objectName(row.entity) }}</td><td>{{ row.indicator }}</td><td>{{ describeReading(row) }}</td><td>{{ qualityNames[row.quality] }}</td><td class="mono">{{ row.revision }}</td></tr></tbody>
          </table>
        </div>
        <p v-else class="dialog-intro">该范围没有符合当前支持条件的记录。</p>
        <p class="version-line">研究输入版本 <code class="mono" :title="report.input_version">{{ report.input_version.slice(0, 12) }}…</code></p>
      </template>
      <template v-if="detailRows.length" #footer>
        <div class="detail-pagination">
          <span role="status">第 {{ detailPage }} 页</span>
          <button class="btn" type="button" :disabled="detailPage === 1" @click="navigateDetails(-1)"><ArrowLeft aria-hidden="true" />上一页</button>
          <button class="btn" type="button" :disabled="!nextCursor" @click="navigateDetails(1)">下一页<ArrowRight aria-hidden="true" /></button>
        </div>
      </template>
    </ElDialog>

    <ElDialog v-model="saveOpen" title="保存分析方案" width="490px" append-to-body>
      <form id="save-research" @submit.prevent="submitPlan">
        <p class="dialog-intro">保存当前已应用的条件。此演示会话中，可用同一方案重新执行分析。</p>
        <div class="dialog-field"><label class="field-label" for="research-plan-title">方案名称</label><input id="research-plan-title" v-model="planTitle" type="text" maxlength="100" autocomplete="off" /></div>
        <p v-if="saveError" class="research-error inside-dialog" role="alert">{{ saveError }}</p>
      </form>
      <div v-if="plans.length" class="saved-plans">
        <h3>本次演示中已保存</h3>
        <ul><li v-for="plan in plans" :key="plan.id"><span>{{ plan.title }}</span><button type="button" class="link-btn" :aria-label="`运行方案 ${plan.title}`" @click="rerunPlan(plan.id)">重新分析</button></li></ul>
      </div>
      <template #footer><button type="submit" form="save-research" class="btn primary">保存当前条件</button></template>
    </ElDialog>

    <ElDialog v-model="exportOpen" title="导出研究明细" width="520px" append-to-body>
      <p class="dialog-intro">CSV 包含获准的已收到记录，沿用当前分析的研究输入版本。下载时会再次校验来源许可与版本。</p>
      <dl class="export-summary">
        <div><dt>导出范围</dt><dd>F 合成演示 · {{ productName(productCode(report.selection)) }}</dd></div>
        <div><dt>对象</dt><dd>{{ resultSubjects }}</dd></div>
        <div><dt>研究窗口</dt><dd>{{ resultDate }} · UTC+8</dd></div>
        <div v-if="ticket"><dt>票据截止时间</dt><dd>{{ localTime(ticket.expires_at) }}（UTC+8）</dd></div>
      </dl>
      <p v-if="exportError" class="research-error inside-dialog" role="alert">{{ exportError }}</p>
      <p v-else-if="ticket" class="ticket-ready" role="status">下载票据已签发，有效期最多 15 分钟。</p>
      <template #footer>
        <button v-if="!ticket || exportError" type="button" class="btn primary" @click="prepareExport">申请下载票据</button>
        <button v-else type="button" class="btn primary" @click="download"><Download aria-hidden="true" />下载 CSV</button>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped src="./analysis.css"></style>
