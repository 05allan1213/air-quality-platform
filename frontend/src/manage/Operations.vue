<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Search, Grid } from '@element-plus/icons-vue'
import { ApiError, api, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import OperationsServices from './OperationsServices.vue'
import OperationsAudit from './OperationsAudit.vue'
import OperationsGrants from './OperationsGrants.vue'
import {
  clockText, filterError, filterParams, filtersFromRoute, ratioText, secondsText, stateLabel,
  allowedAuditKinds, operationsIndicators, periodError, timingText, type Health, type OperationsProduct,
  type OperationsProductPage, type OpsFilters, type Trend, type WindowPage,
} from './operationsContract'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const healthRead = usePrivateRead<Health>()
const catalogRead = usePrivateRead<OperationsProductPage>()
const catalogRows = ref<OperationsProduct[]>([])
const catalogPeriod = ref<{ start: string; end: string } | null>(null)
const nextProduct = ref<string | null>(null)
const verifiedRead = usePrivateRead<OperationsProductPage>()
const verifiedProduct = computed(() => verifiedRead.data.value?.results.find(row => String(row.product) === filters.value.product))
const windowsRead = usePrivateRead<WindowPage>()
const windowRead = usePrivateRead<Health>()
const trendRead = usePrivateRead<Trend>()
const draft = ref<OpsFilters>(filtersFromRoute(route.query))
const windowRows = ref<WindowPage['results']>([])
const errorMessage = ref('')
const selectedWindow = ref('')
const trendExpanded = ref(false)
const filters = computed(() => filtersFromRoute(route.query))
const validVerifiedSelection = computed(() => verifiedProduct.value && (
  !filters.value.indicator || operationsIndicators(verifiedProduct.value.shape).includes(filters.value.indicator)
))
const draftProduct = computed(() => catalogRows.value.find(row => String(row.product) === draft.value.product) ?? (
  draft.value.product === filters.value.product ? verifiedProduct.value : undefined
))
const draftIndicators = computed(() => operationsIndicators(draftProduct.value?.shape))
const has = (action: string) => session.capabilities.actions.includes(action)
const canDiagnose = computed(() => has('diagnose_operations'))
const canReview = computed(() => canDiagnose.value && has('review_observation'))
const auditKinds = computed(() => allowedAuditKinds(session.capabilities.actions))
const queryError = computed(() => Object.values(filters.value).some(Boolean) ? filterError(filters.value) : '')
const components = computed(() => healthRead.data.value?.components ?? null)
const snapshot = computed(() => windowRead.data.value?.window ?? null)
const trend = computed(() => trendRead.data.value)
const windows = computed(() => windowRows.value)
const nextWindowCursor = computed(() => windowsRead.data.value?.page.next_cursor ?? null)
const scope = computed(() => session.scope === 'synthetic' ? 'synthetic' : 'formal')

function query(pathFilters: OpsFilters) {
  return { ...filterParams(pathFilters), scope: session.scope || undefined }
}

function showError(cause: ApiError, windowRelated = true) {
  if (cause.status === 403 || cause.status === 404) {
    if (windowRelated) {
      clearCatalog()
      clearWindowData()
      if (cause.code === 'permission_denied') void session.refresh().catch(() => undefined)
    } else {
      clearOperations()
      if (cause.code === 'permission_denied') void session.refresh().catch(() => undefined)
    }
    errorMessage.value = '诊断权限或所选对象当前不可用，请重新读取。'
  } else if (cause.status === 409 || cause.code === 'invalid_cursor') {
    clearCatalog()
    clearWindowData()
    errorMessage.value = '窗口目录或授权已变化，请从第一页重新读取。'
  } else errorMessage.value = cause.message
}

function clearCatalog() {
  catalogRead.clear()
  catalogRows.value = []
  catalogPeriod.value = null
  nextProduct.value = null
}

function clearWindowData() {
  verifiedRead.clear()
  windowsRead.clear()
  windowRead.clear()
  trendRead.clear()
  windowRows.value = []
  selectedWindow.value = ''
}

function clearOperations() {
  healthRead.clear()
  clearCatalog()
  clearWindowData()
  draft.value = filtersFromRoute({})
  trendExpanded.value = false
  errorMessage.value = ''
}

window.addEventListener('air-quality:clear-reads', clearOperations)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', clearOperations))

async function loadHealth() {
  if (!canDiagnose.value) return
  errorMessage.value = ''
  const result = await healthRead.run(signal => api.get<Health>('admin/operations/health/', undefined, signal))
  if (!result && healthRead.error.value) showError(healthRead.error.value, false)
}

function productParams(period: { start: string; end: string }) {
  return { ...period, purpose: 'operations', scope: session.scope || undefined }
}

async function browseProducts(cursor?: string) {
  if (!canReview.value) return
  const period = { start: draft.value.start, end: draft.value.end }
  const invalid = periodError(period.start, period.end)
  if (invalid) { errorMessage.value = invalid; return }
  errorMessage.value = ''
  if (!cursor) {
    catalogRows.value = []
    nextProduct.value = null
    draft.value.product = ''
    draft.value.indicator = ''
  }
  catalogPeriod.value = period
  const page = await catalogRead.run(signal => api.get<OperationsProductPage>('admin/catalog/products/', {
    ...productParams(period), cursor,
  }, signal))
  if (page) {
    catalogRows.value = cursor ? [...catalogRows.value, ...page.results] : page.results
    nextProduct.value = page.next_cursor
  } else if (catalogRead.error.value) showError(catalogRead.error.value)
}

async function verifyProduct() {
  const selected = filters.value
  const result = await verifiedRead.run(signal => api.get<OperationsProductPage>('admin/catalog/products/', {
    ...productParams({ start: selected.start, end: selected.end }), product: selected.product,
  }, signal))
  if (verifiedRead.error.value) showError(verifiedRead.error.value)
  return result?.results.some(row => String(row.product) === selected.product)
}

async function loadWindows(cursor?: string) {
  if (!canReview.value || !validVerifiedSelection.value) return
  errorMessage.value = ''
  const params = { ...query(filters.value), page_size: 50, cursor }
  const page = await windowsRead.run(signal => api.get<WindowPage>('admin/collection-windows/', params, signal))
  if (!page) {
    if (windowsRead.error.value) showError(windowsRead.error.value)
    return
  }
  const rows = page.results.filter(row => row.product === verifiedProduct.value?.product && row.synthetic === (session.scope === 'synthetic'))
  windowRows.value = cursor ? [...windowRows.value, ...rows] : rows
  if (!cursor && selectedWindow.value && !rows.some(row => String(row.id) === selectedWindow.value)) {
    selectedWindow.value = ''
    windowRead.clear()
  }
}

async function loadWindow(id: string) {
  if (!windows.value.some(row => String(row.id) === id)) return
  selectedWindow.value = id
  errorMessage.value = ''
  const params: Record<string, string> = { window: id, include: 'first_valid_write' }
  if (filters.value.object_code) params.object_code = filters.value.object_code
  if (filters.value.indicator) params.indicator = filters.value.indicator
  const result = await windowRead.run(signal => api.get<Health>('admin/operations/health/', params, signal))
  if (!result && windowRead.error.value && selectedWindow.value === id) {
    selectedWindow.value = ''
    showError(windowRead.error.value)
  }
}

async function loadTrend() {
  if (!canReview.value || !validVerifiedSelection.value) return
  const result = await trendRead.run(signal => api.get<Trend>('admin/operations/trends/', query(filters.value), signal))
  if (!result && trendRead.error.value) showError(trendRead.error.value)
}

async function applyFilters() {
  const invalid = filterError(draft.value)
  if (invalid) {
    errorMessage.value = invalid
    return
  }
  if (!draftProduct.value || (draft.value.indicator && !draftIndicators.value.includes(draft.value.indicator))) {
    errorMessage.value = '请选择获权产品适用的指标。'
    return
  }
  errorMessage.value = ''
  const applied = { ...query(draft.value) }
  if (router.resolve({ name: 'manage-operations', query: applied }).fullPath === route.fullPath) {
    await loadSelection()
  } else {
    await router.replace({ name: 'manage-operations', query: applied })
  }
}

function clearFilters() {
  clearCatalog()
  clearWindowData()
  draft.value = filtersFromRoute({})
  errorMessage.value = ''
  void router.replace({ name: 'manage-operations', query: session.scope ? { scope: session.scope } : {} })
}

async function refreshAll() {
  await Promise.all([loadHealth(), loadSelection()])
}

function nextWindows() {
  if (nextWindowCursor.value) void loadWindows(nextWindowCursor.value)
}

function toggleTrend() {
  trendExpanded.value = !trendExpanded.value
  if (trendExpanded.value && filters.value.start && filters.value.end) void loadTrend()
  else trendRead.clear()
}

async function loadSelection() {
  const restoreWindow = selectedWindow.value
  clearWindowData()
  if (!canReview.value || !filters.value.product || queryError.value || !await verifyProduct()) return
  if (!validVerifiedSelection.value) {
    errorMessage.value = '所选指标不适用于此产品。请修改筛选后重读。'
    return
  }
  await Promise.all([loadWindows(), trendExpanded.value ? loadTrend() : Promise.resolve()])
  if (restoreWindow && windows.value.some(row => String(row.id) === restoreWindow)) await loadWindow(restoreWindow)
}

watch(() => [draft.value.start, draft.value.end], () => {
  const period = catalogPeriod.value
  if (period && (draft.value.start !== period.start || draft.value.end !== period.end)) {
    clearCatalog()
    draft.value.product = ''
    draft.value.indicator = ''
  }
})
watch(() => JSON.stringify([session.scope, canReview.value, filters.value]), () => {
  errorMessage.value = ''
  draft.value = filtersFromRoute(route.query)
  clearWindowData()
  if (!canReview.value) clearCatalog()
  void loadSelection()
}, { immediate: true })
watch(canDiagnose, allowed => {
  if (allowed) void loadHealth()
  else healthRead.clear()
}, { immediate: true })

</script>

<template>
  <section class="operations-page" aria-labelledby="operations-title">
    <header class="page-heading">
      <div>
        <p class="eyebrow">管理工作区 · {{ scope === 'synthetic' ? 'F 合成' : '正式' }}</p>
        <h1 id="operations-title">系统运行</h1>
        <p class="heading-copy">查看运行证据，进入当前账号获准的治理操作。</p>
      </div>
      <button v-if="canDiagnose" class="icon-button refresh-button" type="button" aria-label="重新读取系统状态" :disabled="healthRead.busy.value" @click="refreshAll"><el-icon><Refresh /></el-icon></button>
    </header>

    <p v-if="errorMessage" class="status status-error" role="alert">{{ errorMessage }}</p>

    <section v-if="has('manage_accounts') || has('maintain_city_search_alias')" class="operations-section" aria-labelledby="admin-title">
      <div class="section-heading"><div><h2 id="admin-title">受限管理</h2><p>由原生管理站重核账号或字典的操作权限。</p></div></div>
      <ul class="admin-links">
        <li v-if="has('manage_accounts')"><a href="/admin/auth/user/">账号目录</a></li>
        <li v-if="has('maintain_city_search_alias')"><a href="/admin/observations/city-search-alias/">城市检索别名</a></li>
      </ul>
    </section>

    <section v-if="canDiagnose" class="operations-section" aria-labelledby="services-title">
      <div class="section-heading">
        <div><h2 id="services-title">服务组件</h2><p>证据按各自有效期评估。</p></div>
        <span v-if="healthRead.data.value" class="evaluated">评估于 {{ clockText(healthRead.data.value.components.web.observed_at) }}</span>
      </div>
      <p v-if="healthRead.busy.value && !components" class="status" role="status">正在读取组件状态…</p>
      <p v-else-if="healthRead.error.value" class="status status-warning" role="status">{{ healthRead.error.value.message }}</p>
      <OperationsServices v-else-if="components" :states="components" />
    </section>

    <section v-if="canReview" class="operations-section" aria-labelledby="window-title">
      <div class="section-heading">
        <div><h2 id="window-title">业务窗口诊断</h2><p>窗口和指标只在当前保留、审阅范围内投影。</p></div>
      </div>
      <form class="filter-form" @submit.prevent="applyFilters">
        <label>起始日期<input v-model="draft.start" type="date"></label>
        <label>结束日期<input v-model="draft.end" type="date"></label>
        <button class="btn" type="button" :disabled="catalogRead.busy.value" @click="browseProducts()">查找获权产品</button>
        <label>获权产品<select v-model="draft.product" :disabled="!catalogRows.length && !verifiedProduct" @change="draft.indicator = ''">
          <option value="">选择产品</option>
          <option v-if="verifiedProduct && !catalogRows.some(row => row.product === verifiedProduct?.product)" :value="String(verifiedProduct.product)">{{ verifiedProduct.source }} · {{ verifiedProduct.shape }} · {{ verifiedProduct.product }}</option>
          <option v-for="item in catalogRows" :key="item.product" :value="String(item.product)">{{ item.source }} · {{ item.shape }} · {{ item.product }}</option>
        </select></label>
        <label>对象代码<input v-model="draft.object_code" autocomplete="off" placeholder="可选"></label>
        <label>指标<select v-model="draft.indicator" :disabled="!draftProduct"><option value="">全部</option><option v-for="item in draftIndicators" :key="item" :value="item">{{ item }}</option></select></label>
        <div class="filter-actions"><button class="btn primary" type="submit" :disabled="!draft.product"><el-icon><Search /></el-icon><span>应用</span></button><button class="btn" type="button" @click="clearFilters">清除</button></div>
        <p v-if="queryError" class="form-error filter-error">{{ queryError }}</p>
      </form>
      <p v-if="catalogRead.busy.value" class="quiet" role="status">正在读取获权产品…</p>
      <p v-else-if="catalogPeriod && !catalogRows.length" class="quiet" role="status">该日期范围没有可用于诊断的产品。</p>
      <button v-if="nextProduct" class="btn" type="button" :disabled="catalogRead.busy.value" @click="browseProducts(nextProduct)">读取更多产品</button>
      <p v-if="verifiedRead.busy.value" class="quiet" role="status">正在核验所选产品…</p>
      <p v-else-if="verifiedRead.data.value && !verifiedProduct" class="quiet" role="status">该产品在此日期范围不可用。</p>
      <p v-if="verifiedProduct" class="filter-summary">当前筛选：{{ verifiedProduct.source }} · {{ verifiedProduct.shape }} · 产品 {{ verifiedProduct.product }}<span v-if="filters.start"> · {{ filters.start }} 至 {{ filters.end }}（结束日期不含）</span><span v-if="filters.object_code"> · 对象 {{ filters.object_code }}</span><span v-if="filters.indicator"> · {{ filters.indicator }}</span></p>

      <div v-if="validVerifiedSelection" class="window-grid">
        <div class="window-picker">
          <div class="subheading"><h3>获权窗口</h3><span>{{ windows.length }} 个可见</span></div>
          <p v-if="windowsRead.busy.value && !windows.length" class="quiet" role="status">正在读取窗口目录…</p>
          <p v-else-if="windowsRead.error.value" class="quiet" role="status">{{ windowsRead.error.value.message }}</p>
          <p v-else-if="!windows.length" class="quiet">当前条件下没有可显示的窗口。</p>
          <ul v-else class="window-list" aria-label="获权采集窗口">
            <li v-for="item in windows" :key="item.id">
              <button type="button" class="window-option" :aria-pressed="selectedWindow === String(item.id)" @click="loadWindow(String(item.id))">
                <span class="window-time">{{ clockText(item.target_end) }}</span>
                <span class="window-meta">窗口 {{ item.id }} · {{ item.full_scope ? stateLabel[item.result ?? ''] ?? item.result ?? '结果未披露' : '部分范围' }}</span>
              </button>
            </li>
          </ul>
          <button v-if="nextWindowCursor" class="text-button more-button" type="button" :disabled="windowsRead.busy.value" @click="nextWindows">继续读取</button>
        </div>

        <div class="window-detail" aria-live="polite">
          <template v-if="snapshot">
            <div class="subheading"><div><h3>窗口 {{ snapshot.window_id }}</h3><p>状态：{{ stateLabel[snapshot.state] ?? snapshot.state }}<span v-if="snapshot.result"> · {{ stateLabel[snapshot.result] ?? snapshot.result }}</span></p></div><span class="evaluated">获权页</span></div>
            <RouterLink class="window-link" :to="{ name: 'manage-quality', query: { scope: session.scope || undefined, product: filters.product, window: String(snapshot.window_id) } }">查看窗口证据</RouterLink>
            <template v-if="snapshot.coverage">
              <dl class="coverage-stats">
                <div><dt>E 预期</dt><dd>{{ snapshot.coverage.E ?? '未知' }}</dd></div><div><dt>O 收到</dt><dd>{{ snapshot.coverage.O }}</dd></div><div><dt>V 有效</dt><dd>{{ snapshot.coverage.V }}</dd></div>
              </dl>
              <dl class="queue-stats">
                <div><dt>可见待处理</dt><dd>{{ snapshot.visible_pending }}<small>排队 {{ snapshot.visible_sent_not_started }} · 配额等待 {{ snapshot.visible_quota_wait }}</small></dd></div>
                <div><dt>待确认投递</dt><dd>{{ snapshot.visible_unconfirmed_dispatch }}</dd></div>
                <div><dt>可见重试</dt><dd>{{ snapshot.visible_retry }}</dd></div>
                <div><dt>执行中</dt><dd>{{ snapshot.visible_running }}</dd></div>
                <div><dt>最早可见排队</dt><dd><time :datetime="snapshot.oldest_visible_pending_created_at ?? undefined">{{ clockText(snapshot.oldest_visible_pending_created_at) }}</time><small>年龄 {{ secondsText(snapshot.oldest_visible_pending_age_seconds) }}</small></dd></div>
                <div><dt>下次配额检查</dt><dd><time :datetime="snapshot.next_visible_quota_check_at ?? undefined">{{ clockText(snapshot.next_visible_quota_check_at) }}</time></dd></div>
              </dl>
              <p v-if="snapshot.truncated" class="status status-warning">运行列表已截断，以上积压指标只计当前获权返回页。</p>
              <p v-if="snapshot.state === 'partially_authorized'" class="status status-warning">仅显示当前获权部分；不推断窗口总量或权外运行状态。</p>
            </template>
            <p v-else class="quiet">窗口覆盖当前不可用。</p>
            <dl class="time-provenance">
              <div><dt>源观测时标</dt><dd><time :datetime="snapshot.coverage?.latest_valid_at ?? undefined">{{ clockText(snapshot.coverage?.latest_valid_at) }}</time><small>覆盖中最近有效观测</small></dd></div>
              <div><dt>抓取时刻</dt><dd><time :datetime="snapshot.latest_visible_fetched_at ?? undefined">{{ clockText(snapshot.latest_visible_fetched_at) }}</time><small>获权运行页</small></dd></div>
              <div><dt>首次有效入库</dt><dd><time :datetime="snapshot.latest_first_valid_write_at ?? undefined">{{ clockText(snapshot.latest_first_valid_write_at) }}</time><small>{{ snapshot.first_valid_write_state === 'known' ? '当前获权项目页最近时刻' : snapshot.first_valid_write_state === 'none' ? '当前获权项目页内无有效写入' : '证据或披露范围不足' }}</small></dd></div>
            </dl>
            <p v-if="snapshot.first_valid_write_page_truncated" class="status status-warning">首次有效时刻只覆盖当前显示项目页，后续项目页尚未核对。</p>
            <p v-if="snapshot.first_valid_write_state === 'unavailable'" class="status status-warning">无法证明首次有效写入时刻；不会用窗口首次响应或当前观测时间替代。</p>
            <div class="error-list"><h4>获权运行错误摘要</h4><p v-if="!snapshot.visible_errors.length" class="quiet">当前返回页无可显示错误码。</p><ul v-else><li v-for="(item, index) in snapshot.visible_errors" :key="`${item.error_code}-${index}`"><code>{{ item.error_code }}</code><span>{{ stateLabel[item.result] ?? item.result }}</span><span>响应 {{ stateLabel[item.request_state] ?? item.request_state }}</span></li></ul></div>
          </template>
          <p v-else-if="windowRead.busy.value" class="quiet" role="status">正在核验窗口权限与账本…</p>
          <p v-else-if="windowRead.error.value" class="status status-warning" role="status">{{ windowRead.error.value.message }}</p>
          <p v-else class="empty-detail"><el-icon><Grid /></el-icon><span>选择一个获权窗口查看覆盖与运行证据。</span></p>
        </div>
      </div>
      <p v-else class="quiet">选择源地方日起止日期（结束日期不含），查找获权产品后读取窗口。</p>
    </section>

    <section v-if="canReview" class="operations-section trend-section" aria-labelledby="trend-title">
      <div class="section-heading">
        <div><h2 id="trend-title">窗口日趋势</h2><p>按产品源时区聚合的 E/O/V 与执行耗时。</p></div>
        <button class="btn" type="button" :aria-expanded="trendExpanded" :disabled="!validVerifiedSelection && !trendExpanded" @click="toggleTrend">{{ trendExpanded ? '收起趋势' : '读取趋势' }}</button>
      </div>
      <template v-if="trendExpanded">
        <p v-if="!validVerifiedSelection" class="quiet">先应用获权产品及不超过 30 天的日期区间。</p>
        <p v-else-if="trendRead.busy.value" class="quiet" role="status">正在读取获权日趋势…</p>
        <p v-else-if="trendRead.error.value" class="status status-warning" role="status">{{ trendRead.error.value.message }}</p>
        <p v-else-if="trend && !trend.buckets.length" class="quiet" role="status">此范围没有可披露的日趋势。</p>
        <div v-else-if="trend" class="table-scroll" tabindex="0" role="region" aria-label="窗口日趋势表格，可横向滚动">
          <table><caption>{{ trend.bucket_timezone }} · {{ trend.start }} 至 {{ trend.end }}（结束日期不含） · 评估于 {{ clockText(trend.evaluated_at) }}</caption><thead><tr><th scope="col">源地方日</th><th scope="col">窗口</th><th scope="col">部分获权</th><th scope="col">E 预期</th><th scope="col">O 收到</th><th scope="col">V 有效</th><th scope="col">收到率</th><th scope="col">有效率</th><th scope="col">首次观测窗率</th><th scope="col">终结响应率</th><th scope="col">平均排队</th><th scope="col">平均执行</th><th scope="col">平均排期</th></tr></thead><tbody><tr v-for="bucket in trend.buckets" :key="bucket.date"><th scope="row">{{ bucket.date }}</th><td>{{ bucket.window_count }}</td><td>{{ bucket.partially_authorized_windows }}</td><td>{{ bucket.coverage.E ?? `未知 (${bucket.coverage.unknown_E_windows} 窗)` }}</td><td>{{ bucket.coverage.O }}</td><td>{{ bucket.coverage.V }}</td><td>{{ ratioText(bucket.coverage.received) }}</td><td>{{ ratioText(bucket.coverage.valid) }}</td><td>{{ ratioText(bucket.first_observation_window_rate) }}</td><td>{{ ratioText(bucket.response_attempt_rate) }}</td><td>{{ timingText(bucket.queue_wait) }}</td><td>{{ timingText(bucket.execution) }}</td><td>{{ timingText(bucket.schedule_delay) }}</td></tr></tbody></table>
        </div>
        <p v-if="trend && trend.buckets.some(bucket => bucket.source_publication_delay === null)" class="quiet">源发布时间无记录，不以抓取时刻代替。</p>
      </template>
    </section>
    <OperationsAudit v-if="auditKinds.length" :kinds="auditKinds" />
    <OperationsGrants v-if="has('manage_grants')" />
  </section>
</template>

<style scoped>
.operations-page { display: grid; gap: 0; min-width: 0; }
.page-heading, .section-heading, .subheading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-heading { align-items: center; }
.page-heading h1 { margin-bottom: 6px; }
.eyebrow { margin: 0 0 4px; color: var(--aq-link); font-size: .8rem; font-weight: 650; }
.heading-copy, .section-heading p, .subheading p { margin: 3px 0 0; color: var(--aq-secondary); font-size: .88rem; }
.refresh-button { flex: 0 0 42px; border: 1px solid var(--aq-control-border); background: #f5faff; }
.operations-section { min-width: 0; padding: 20px 0; border-bottom: 1px solid var(--aq-border); }
.operations-section h2 { margin-bottom: 0; }
.admin-links { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 12px 0 0; padding: 0; list-style: none; }
.admin-links a { display: inline-flex; align-items: center; min-height: 40px; }
.evaluated, .subheading > span { color: var(--aq-muted); font-size: .8rem; text-align: end; }
.filter-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: end; gap: 10px; margin: 16px 0 8px; }
.filter-form label { display: grid; gap: 4px; color: var(--aq-secondary); font-size: .81rem; font-weight: 620; }
.filter-form input, .filter-form select { width: 100%; min-width: 0; min-height: 38px; padding: 6px 8px; border: 1px solid var(--aq-control-border); border-radius: 6px; background: #f9fcff; color: var(--aq-text); }
.filter-actions { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
.filter-actions .btn { min-height: 38px; }
.filter-error { grid-column: 1 / -1; margin: 0; }
.filter-summary { margin: 8px 0 12px; font-size: .82rem; overflow-wrap: anywhere; }
.window-grid { display: grid; grid-template-columns: minmax(225px, .85fr) minmax(0, 2fr); border-top: 1px solid var(--aq-border); }
.window-picker { min-width: 0; padding: 14px 16px 0 0; border-right: 1px solid var(--aq-border); }
.window-detail { min-width: 0; padding: 14px 0 0 18px; }
.window-link { display: inline-flex; align-items: center; min-height: 36px; }
.subheading { align-items: baseline; }
.subheading h3 { margin: 0; font-size: .97rem; }
.window-list { max-height: 390px; overflow: auto; margin: 10px 0 0; padding: 0; list-style: none; border-top: 1px solid var(--aq-border); }
.window-option { display: grid; width: 100%; min-height: 58px; gap: 2px; padding: 8px 10px; border: 0; border-bottom: 1px solid var(--aq-border); background: transparent; color: var(--aq-text); text-align: start; cursor: pointer; }
.window-option:hover { background: #edf7fc; }
.window-option[aria-pressed='true'] { border-inline-start: 3px solid var(--aq-brand); background: #e5f2fa; }
.window-time { font-size: .87rem; font-weight: 640; }
.window-meta { color: var(--aq-secondary); font-size: .79rem; }
.more-button { margin-top: 8px; }
.coverage-stats, .queue-stats, .time-provenance { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 13px 0; border-top: 1px solid var(--aq-border); border-left: 1px solid var(--aq-border); }
.coverage-stats > div, .queue-stats > div, .time-provenance > div { min-width: 0; padding: 9px 10px; border-right: 1px solid var(--aq-border); border-bottom: 1px solid var(--aq-border); }
.coverage-stats dt, .queue-stats dt, .time-provenance dt { color: var(--aq-secondary); font-size: .78rem; }
.coverage-stats dd, .queue-stats dd, .time-provenance dd { margin: 2px 0 0; font-weight: 650; overflow-wrap: anywhere; }
.queue-stats dd { font-size: .91rem; }
.queue-stats small, .time-provenance small { display: block; margin-top: 3px; color: var(--aq-secondary); font-size: .77rem; font-weight: 400; }
.time-provenance { grid-template-columns: repeat(3, minmax(0, 1fr)); background: #e9f1ed80; }
.status { margin: 10px 0; }
.error-list { padding-top: 8px; border-top: 1px solid var(--aq-border); }
.error-list h4 { margin: 0 0 7px; font-size: .86rem; }
.error-list ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.error-list li { display: flex; flex-wrap: wrap; gap: 5px 12px; align-items: center; padding: 7px 9px; border-left: 3px solid var(--aq-danger); background: #f9e7e888; font-size: .8rem; }
.error-list code { color: var(--aq-danger); font-weight: 650; }
.quiet { margin: 10px 0; color: var(--aq-secondary); font-size: .86rem; }
.empty-detail { display: flex; min-height: 100px; align-items: center; justify-content: center; gap: 9px; color: var(--aq-secondary); }
.trend-section .section-heading { align-items: center; }
.trend-section > .quiet:last-child { margin-bottom: 0; }
.table-scroll { overflow: auto; margin-top: 12px; border: 1px solid var(--aq-border); background: #fbfdff; }
table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: .81rem; }
caption { padding: 9px 10px; color: var(--aq-secondary); text-align: start; }
th, td { padding: 8px 10px; border-top: 1px solid var(--aq-border); text-align: end; }
thead th { position: sticky; top: 0; background: #dceaf5; }
th:first-child, td:first-child { text-align: start; }
@media (max-width: 1050px) {
  .filter-form { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .window-grid { grid-template-columns: minmax(0, 1fr); }
  .window-picker { padding: 14px 0; border-right: 0; border-bottom: 1px solid var(--aq-border); }
  .window-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); max-height: 230px; }
  .window-detail { padding: 14px 0 0; }
}
@media (max-width: 620px) {
  .operations-section { padding: 14px 12px; }
  .page-heading { align-items: flex-start; }
  .section-heading { flex-wrap: wrap; }
  .filter-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-actions { grid-column: 1 / -1; }
  .window-list { grid-template-columns: minmax(0, 1fr); }
  .coverage-stats, .queue-stats, .time-provenance { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 360px) {
  .operations-section { padding-inline: 9px; }
  .filter-form { grid-template-columns: minmax(0, 1fr); }
  .filter-actions, .filter-error { grid-column: auto; }
  .coverage-stats, .queue-stats, .time-provenance { grid-template-columns: minmax(0, 1fr); }
}
</style>
