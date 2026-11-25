<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Clock, Star } from '@element-plus/icons-vue'
import { api, ApiError, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import CityStationDirectory from './CityStationDirectory.vue'
import { gradeLabel } from './HomeProjection'
import { localApiHref, readFavorite, useMonitorPages } from './reads'
import TrendChart from './TrendChart.vue'

type Observation = {
  indicator: string
  value: string | null
  unit: string
  quality: string
  grade: number | null
  color: string | null
  comparable: boolean
  standard_series: string
  observed_at: string
  fetched_at: string | null
  age_seconds: number | null
  freshness: string
  primary_pollutant?: string | null
  source_local_date?: string | null
  revision: number
  revision_no: number
  attribution?: string | null
  rights_attributions?: string[]
}
type CityResponse = {
  product: number; city: number; city_name: string | null; source: string; synthetic: boolean
  snapshot_at: string | null; evaluated_at: string; standard_series: string | null; state: string
  slots: { indicator: string; state: string; results: Observation[] }[]
  links: { city?: { product: number; href: string }[]; trend?: { product: number; href: string }[]; stations?: { product: number; href: string }[]; history?: string[] }
}
type TrendResponse = { product: number; synthetic: boolean; anchor: string | null; anchor_basis: string; state: string; points: { observed_at: string; state: string; results: Observation[] }[] }
type DetailResponse = { state: string; selection: Record<string, unknown>; evaluated_at: string; results: Observation[]; page: { page_size: number; next_cursor: string | null } }
type Favorite = { id: number; kind: string; target_id?: number; product?: number; state: string }
type DiscoveryItem = { product: number; shape: string; source: string; city?: number; name: string | null; local_report_date: string | null; observed_at: string | null; indicator: string; attribution: string | null; link: string }
type DiscoveryPage = { results: DiscoveryItem[]; page: { page_size: number; next_cursor: string | null } }
type CurrentResponse = { state: string; results: Observation[]; reason?: string }

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const cityRead = usePrivateRead<CityResponse>()
const trendRead = usePrivateRead<TrendResponse>()
const favoriteRead = usePrivateRead<Favorite | null>()
const detailRead = useMonitorPages<DetailResponse>()
const stationDirectoryReads = ref<{ product: number; href: string }[]>([])
const discoveryRead = usePrivateRead<DiscoveryPage>()
const dailyRead = usePrivateRead<CurrentResponse>()
const selectedProduct = computed(() => typeof route.query.product === 'string' ? route.query.product : '')
const cityId = computed(() => String(route.params.id))
const scope = computed(() => session.scope === 'synthetic' ? 'synthetic' : undefined)
const trendLink = computed(() => cityRead.data.value?.links.trend?.[0]?.href ?? '')
const stationLinks = computed(() => cityRead.data.value?.links.stations ?? [])
const historyLinks = computed(() => cityRead.data.value?.links.history ?? [])
const favoriteItem = computed(() => favoriteRead.data.value)
const busyFavorite = ref(false)
const favoriteMessage = ref('')
const dailyItems = ref<DiscoveryItem[]>([])
const dailyCursor = ref<string | null>(null)
const indicators = ['AQI', 'PM2.5', 'PM10', 'SO2', 'NO2', 'CO', 'O3']
const indicatorLabels: Record<string, string> = { AQI: 'AQI', 'PM2.5': 'PM₂.₅', PM10: 'PM₁₀', SO2: 'SO₂', NO2: 'NO₂', CO: 'CO', O3: 'O₃' }

function paramsFromRoute() {
  const params: Record<string, string> = { product: selectedProduct.value }
  if (scope.value) params.scope = scope.value
  for (const key of ['at', 'as_of', 'standard_series'] as const) {
    const value = route.query[key]
    if (typeof value === 'string' && value) params[key] = value
  }
  return params
}
watch(() => [cityId.value, selectedProduct.value, route.query.at, route.query.as_of, route.query.standard_series, session.scope], () => {
  detailRead.clear()
  trendRead.clear()
  stationDirectoryReads.value = []
  discoveryRead.clear()
  dailyRead.clear()
  dailyItems.value = []
  dailyCursor.value = null
  if (!selectedProduct.value || !/^\d+$/.test(cityId.value)) { cityRead.clear(); favoriteRead.clear(); return }
  void cityRead.run(signal => api.get<CityResponse>(`cities/${cityId.value}/`, paramsFromRoute(), signal))
  void favoriteRead.run(signal => readFavorite('city', cityId.value, selectedProduct.value, scope.value, signal))
}, { immediate: true })

watch(() => trendLink.value, href => {
  const target = localApiHref(href)
  if (!target) { trendRead.clear(); return }
  void trendRead.run(signal => api.get<TrendResponse>(target.path, target.params, signal))
}, { immediate: true })

watch(stationLinks, links => { stationDirectoryReads.value = links.map(item => ({ product: item.product, href: item.href })) }, { immediate: true })

async function toggleFavorite() {
  if (!cityRead.data.value || busyFavorite.value) return
  busyFavorite.value = true
  favoriteMessage.value = ''
  try {
    if (favoriteItem.value) {
      const query = scope.value ? `?scope=${scope.value}` : ''
      await api.delete(`me/favorites/${favoriteItem.value.id}/${query}`)
      favoriteMessage.value = '已取消关注。'
    } else {
      const query = scope.value ? `?scope=${scope.value}` : ''
      await api.post(`me/favorites/${query}`, { kind: 'city', target_id: cityId.value, product: selectedProduct.value })
      favoriteMessage.value = '已加入我的关注。'
    }
    void favoriteRead.run(signal => readFavorite('city', cityId.value, selectedProduct.value, scope.value, signal))
  } catch (error) {
    favoriteMessage.value = error instanceof ApiError && error.code === 'favorite_exists' ? '此城市已在关注列表中。' : '关注操作未完成，请重新读取后再试。'
  } finally { busyFavorite.value = false }
}

async function loadHistory(href: string) {
  const target = localApiHref(href)
  if (!target) { detailRead.clear(); return }
  detailRead.open(target)
}
async function findDailyReports() {
  if (!cityRead.data.value) return
  const params: Record<string, string | number> = { page_size: 50 }
  if (scope.value) params.scope = scope.value
  if (dailyCursor.value) params.cursor = dailyCursor.value
  if (cityRead.data.value.snapshot_at) params.at = cityRead.data.value.snapshot_at
  const page = await discoveryRead.run(signal => api.get<DiscoveryPage>('catalog/objects/', params, signal))
  if (!page) return
  const matches = page.results.filter(item => item.shape === 'D' && String(item.city) === cityId.value)
  dailyItems.value = [...dailyItems.value, ...matches]
  dailyCursor.value = page.page.next_cursor
}
async function nextDailyPage() { await findDailyReports() }
async function openDailyReport(item: DiscoveryItem) {
  const target = localApiHref(item.link)
  if (!target || target.path !== 'observations/current/') return
  await dailyRead.run(signal => api.get<CurrentResponse>(target.path, target.params, signal))
}
async function openHref(href: string) {
  const target = localApiHref(href)
  if (!target) return
  const url = new URL(href, window.location.origin)
  if (url.origin !== window.location.origin) return
  const cityMatch = url.pathname.match(/^\/api\/v1\/cities\/(\d+)\/$/)
  const stationMatch = url.pathname.match(/^\/api\/v1\/stations\/(\d+)\/$/)
  const stationQuery = Object.fromEntries([...url.searchParams].filter(([key]) => ['product', 'site_epoch', 'as_of', 'scope'].includes(key)))
  if (cityMatch) await router.push({ name: 'city', params: { id: cityMatch[1] }, query: target.params })
  else if (stationMatch) await router.push({ name: 'station', params: { id: stationMatch[1] }, query: stationQuery })
}
function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'not_authenticated') return '登录状态已失效，私有观测已清除。请重新登录。'
    if (error.code === 'use_denied') return '此城市当前没有可披露的记录。'
    if (error.code === 'not_found') return '该城市当前不可访问。'
    if (error.code === 'invalid_request') return '所选产品或时刻不符合数据服务的条件。'
  }
  return '城市记录暂时无法读取，请重试。'
}
const errorMessage = computed(() => cityRead.error.value ? errorText(cityRead.error.value) : '')
function qualityLabel(value: string) {
  return ({ valid: '有效', suspect: '待核', missing: '缺测', censored: '删失', invalid: '无效', disputed: '争议', candidate: '候选' } as Record<string, string>)[value] ?? '未知'
}
</script>

<template>
  <div class="monitor-page detail-page">
    <header class="page-heading detail-heading"><div><button class="text-action back-link" type="button" @click="router.back()"><ArrowLeft aria-hidden="true"/>返回上页</button><h1>{{ cityRead.data.value?.city_name ?? `城市 ${cityId}` }}</h1><p class="heading-copy">城市七项指标取自 C 产品。全国 L 快照与独立 T 趋势分别标识，不合并数值。</p></div><div class="heading-tools"><span class="status">{{ cityRead.data.value?.synthetic ? 'F · 合成数据' : 'C · 城市直接观测' }}</span><button class="btn" type="button" :disabled="busyFavorite || !cityRead.data.value" @click="toggleFavorite"><Star aria-hidden="true"/>{{ favoriteItem ? '取消关注' : '关注城市' }}</button></div></header>
    <p v-if="favoriteMessage" class="inline-message" role="status">{{ favoriteMessage }}</p>
    <p v-if="errorMessage" class="error-line" role="alert">{{ errorMessage }} <button class="text-action" type="button" @click="cityRead.run(signal => api.get<CityResponse>(`cities/${cityId}/`, paramsFromRoute(), signal))">重试</button></p>
    <section v-else-if="cityRead.busy.value && !cityRead.data.value" class="panel loading-panel" role="status">正在读取当前获准的城市记录…</section>
    <template v-else-if="cityRead.data.value">
      <section class="detail-facts" aria-label="城市记录信息"><div><Clock aria-hidden="true"/><span>观测时刻</span><strong>{{ cityRead.data.value.snapshot_at ? new Date(cityRead.data.value.snapshot_at).toLocaleString('zh-CN') : '未知' }}</strong></div><div><span>数据来源</span><strong>{{ cityRead.data.value.source }} · 产品 {{ cityRead.data.value.product }}</strong></div><div><span>评估时刻</span><strong>{{ new Date(cityRead.data.value.evaluated_at).toLocaleString('zh-CN') }}</strong></div><div><span>质量说明</span><strong>按指标和修订逐项展示</strong></div></section>
      <section class="panel indicator-panel" aria-labelledby="indicator-title"><div class="section-head"><div><h2 id="indicator-title">C 城市指标</h2><p>零值、缺测、争议与来源时间按服务端投影保留</p></div><span v-if="cityRead.data.value.standard_series" class="status">{{ cityRead.data.value.standard_series }}</span></div><div class="indicator-grid">
        <article v-for="indicator in indicators" :key="indicator" class="indicator-row">
          <div class="indicator-name">{{ indicatorLabels[indicator] }}</div>
          <template v-if="cityRead.data.value.slots.find(slot => slot.indicator === indicator)?.results.length">
            <div v-for="item in cityRead.data.value.slots.find(slot => slot.indicator === indicator)!.results"
              :key="`${item.standard_series}-${item.revision}`" class="indicator-result">
              <strong class="metric-value">{{ item.value ?? (item.quality === 'missing' ? '缺测' : '未知') }}</strong>
              <span class="unit">{{ item.unit }}</span>
              <span class="quality-mark" :data-quality="item.quality">{{ qualityLabel(item.quality) }}</span>
              <small v-if="indicator === 'AQI'">{{ gradeLabel(item.grade) }} · 首要污染物 {{ item.primary_pollutant ?? '未披露' }}</small>
              <small>{{ item.standard_series === 'not_applicable' ? '不适用标准' : item.standard_series }}</small>
              <small>观测 {{ new Date(item.observed_at).toLocaleString('zh-CN') }}</small>
              <small>采集 {{ item.fetched_at ? new Date(item.fetched_at).toLocaleString('zh-CN') : '未知' }}</small>
              <small>修订 {{ item.revision_no }}</small>
            </div>
          </template>
          <div v-else class="missing-result">{{ cityRead.data.value.slots.find(slot => slot.indicator === indicator)?.state === 'unavailable' ? '未获此指标展示' : '本次没有该指标' }}</div>
        </article>
      </div></section>

      <section class="panel trend-panel" aria-labelledby="trend-title"><div class="section-head"><div><h2 id="trend-title">独立 T 城市趋势</h2><p>T 是最近 24 小时 AQI 序列，不并入 C 指标和全国 L 榜单</p></div><span v-if="trendRead.data.value" class="status">T · 产品 {{ trendRead.data.value.product }}</span></div><p v-if="!trendLink" class="quiet-note">当前来源未提供可访问的 T 产品链接。</p><p v-else-if="trendRead.error.value" class="error-line" role="alert">趋势不可用：{{ errorText(trendRead.error.value) }}</p><p v-else-if="trendRead.busy.value && !trendRead.data.value" class="quiet-note" role="status">正在读取独立 T 趋势…</p><template v-else-if="trendRead.data.value"><TrendChart :points="trendRead.data.value.points" :label="`${cityRead.data.value.city_name ?? '城市'}独立 T 产品 24 小时 AQI 趋势，缺失时槽留空`"/><p class="quiet-note">锚点 {{ trendRead.data.value.anchor ? new Date(trendRead.data.value.anchor).toLocaleString('zh-CN') : '未知' }} · {{ trendRead.data.value.state === 'no_data' ? '当前窗口无有效值' : '空时槽保持断开' }}</p></template></section>

      <section class="panel" aria-labelledby="stations-title"><div class="section-head"><div><h2 id="stations-title">同城监测站</h2><p>站点与站址期身份由对应产品目录决定</p></div></div><p v-if="!stationDirectoryReads.length" class="quiet-note">当前没有可披露的站点目录链接。</p><CityStationDirectory v-for="item in stationDirectoryReads" :key="item.product" :href="item.href" :product="item.product" @open="openHref" /></section>
      <section class="panel" aria-labelledby="history-title">
        <div class="section-head"><div><h2 id="history-title">历史数据入口</h2><p>按服务端提供的产品、指标、时间窗和标准请求</p></div></div>
        <div v-if="historyLinks.length" class="history-buttons"><button v-for="(href, index) in historyLinks" :key="href" class="btn" type="button" @click="loadHistory(href)">历史片段 {{ index + 1 }}</button></div>
        <p v-else class="quiet-note">当前没有可披露的历史片段。</p>
        <p v-if="detailRead.error.value" class="error-line" role="alert">该历史片段当前不可读取。</p>
        <div v-else-if="detailRead.data.value" class="table-scroll detail-history-table" tabindex="0">
          <table><thead><tr><th scope="col">观测时刻</th><th scope="col">指标</th><th scope="col">数值</th><th scope="col">质量</th></tr></thead><tbody>
            <tr v-for="item in detailRead.data.value.results" :key="`${item.observed_at}-${item.indicator}-${item.revision}`"><td>{{ new Date(item.observed_at).toLocaleString('zh-CN') }}</td><th scope="row">{{ indicatorLabels[item.indicator] ?? item.indicator }}</th><td>{{ item.value ?? '无值' }} {{ item.unit }}</td><td>{{ qualityLabel(item.quality) }}</td></tr>
            <tr v-if="!detailRead.data.value.results.length"><td colspan="4">该窗口没有可显示记录。</td></tr>
          </tbody></table>
        </div>
        <div class="pager"><button v-if="detailRead.hasCursor.value || detailRead.error.value" class="btn" type="button" :disabled="detailRead.busy.value" @click="detailRead.first">{{ detailRead.hasCursor.value ? '返回历史第一页' : '重新读取历史' }}</button><button v-if="detailRead.data.value?.page.next_cursor" class="btn" type="button" :disabled="detailRead.busy.value" @click="detailRead.next">下一页历史</button></div>
      </section>
      <section class="panel daily-panel" aria-labelledby="daily-title"><div class="section-head"><div><h2 id="daily-title">独立 D 日报告</h2><p>D 与小时 C/T 分开保存和呈现，按服务端披露的源日及精确读取链接访问。</p></div><button class="btn" type="button" :disabled="discoveryRead.busy.value || (Boolean(discoveryRead.data.value) && !dailyCursor)" @click="findDailyReports">{{ discoveryRead.data.value ? '继续查找' : '查找获准报告' }}</button></div><p v-if="discoveryRead.error.value" class="error-line" role="alert">目录读取失败：{{ discoveryRead.error.value.message }}</p><div v-if="dailyItems.length" class="daily-links"><article v-for="item in dailyItems" :key="`${item.product}-${item.local_report_date}-${item.indicator}`" class="daily-row"><div><strong>{{ item.local_report_date ?? '源地方日未知' }} · {{ indicatorLabels[item.indicator] ?? item.indicator }}</strong><span>{{ item.source }} · D 产品 {{ item.product }}</span><small>{{ item.attribution ?? '来源署名未提供' }}</small></div><button class="text-action" type="button" @click="openDailyReport(item)">精确读取 <ArrowRight aria-hidden="true"/></button></article></div><p v-else class="quiet-note">{{ discoveryRead.data.value ? '当前已读目录页没有该城市可披露的 D 报告。可继续检索下一页。' : '按需从获准对象目录查找，不将 C 小时值推算为日报告。' }}</p><div v-if="dailyRead.data.value" class="table-scroll detail-history-table" tabindex="0"><table><thead><tr><th scope="col">源地方日</th><th scope="col">指标</th><th scope="col">值</th><th scope="col">质量</th></tr></thead><tbody><tr v-for="item in dailyRead.data.value.results" :key="`${item.indicator}-${item.revision}`"><td>{{ item.source_local_date ?? '未知' }}</td><th scope="row">{{ indicatorLabels[item.indicator] ?? item.indicator }}</th><td>{{ item.value ?? '无值' }} {{ item.unit }}</td><td>{{ qualityLabel(item.quality) }}</td></tr><tr v-if="!dailyRead.data.value.results.length"><td colspan="4">没有可显示的 D 记录：{{ dailyRead.data.value.state }}。</td></tr></tbody></table></div><button v-if="dailyCursor" class="btn" type="button" :disabled="discoveryRead.busy.value" @click="nextDailyPage">继续查找</button></section>
    </template>
    <section v-else class="panel empty-panel"><h2>需要从获准目录进入城市</h2><p>此页面不猜测产品或城市身份。请从全国列表或本人关注中的有效链接打开城市记录。</p></section>
  </div>
</template>

<style scoped>
.detail-page { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; max-width: 1240px; margin: 0 auto; }
.detail-page .panel { min-width: 0; padding: clamp(12px, 1.5vw, 18px); }
.detail-page .section-head { flex-wrap: wrap; }
.text-action { display: inline-flex; min-height: 32px; align-items: center; gap: 5px; padding: 4px 2px; border: 0; color: var(--aq-link); background: transparent; font-size: 13px; font-weight: 620; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.back-link { width: fit-content; }
.detail-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; }
.detail-heading h1 { margin: 2px 0 0; font-size: 30px; }
.heading-copy { margin: 7px 0 0; color: var(--aq-secondary); font-size: 13px; line-height: 1.55; }
.heading-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
.detail-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; overflow: hidden; border-radius: 8px; background: var(--aq-border); }
.detail-facts > div { display: grid; gap: 4px; min-width: 0; padding: 12px 14px; background: var(--aq-content); }
.detail-facts span { color: var(--aq-muted); font-size: 11px; }
.detail-facts strong { color: var(--aq-text); font-size: 13px; font-weight: 620; overflow-wrap: anywhere; }
.indicator-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 12px; border-top: 1px solid var(--aq-border); }
.indicator-row { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px 10px; min-width: 0; padding: 12px 10px; border-bottom: 1px solid var(--aq-border); }
.indicator-name { grid-row: span 4; color: var(--aq-secondary); font-size: 13px; font-weight: 650; }
.indicator-result { display: flex; flex-wrap: wrap; align-items: baseline; gap: 3px 8px; min-width: 0; }
.metric-value { color: var(--aq-text); font-size: 23px; font-variant-numeric: tabular-nums; }
.unit { color: var(--aq-secondary); font-size: 12px; }
.quality-mark { padding: 2px 6px; border-radius: 4px; background: var(--aq-success-bg); color: var(--aq-success); font-size: 10px; }
.quality-mark[data-quality="missing"], .quality-mark[data-quality="invalid"] { background: var(--aq-warning-bg); color: var(--aq-warning); }
.quality-mark[data-quality="disputed"], .quality-mark[data-quality="suspect"] { background: var(--aq-danger-bg); color: var(--aq-danger); }
.indicator-result small { display: block; flex-basis: 100%; color: var(--aq-muted); font-size: 11px; line-height: 1.45; }
.missing-result { grid-column: 2; color: var(--aq-muted); font-size: 12px; }
.trend-panel { min-height: 340px; }
.history-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.quiet-note { margin: 12px 0 0; color: var(--aq-secondary); font-size: 12px; line-height: 1.55; }
.error-line { color: var(--aq-danger); font-size: 13px; }
.inline-message { color: var(--aq-success); font-size: 12px; }
.detail-history-table { max-height: 300px; margin-top: 12px; overflow: auto; border-radius: 7px; }
.daily-row { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 14px; padding: 10px 0; border-bottom: 1px solid var(--aq-border); }
.daily-row > div { display: grid; gap: 3px; min-width: 0; overflow-wrap: anywhere; }
.daily-row span, .daily-row small { color: var(--aq-secondary); font-size: 12px; }
.detail-history-table table { width: 100%; min-width: 540px; border-collapse: collapse; font-size: 12px; }
.detail-history-table th, .detail-history-table td { padding: 8px 10px; border-bottom: 1px solid var(--aq-border); text-align: left; white-space: nowrap; }
.detail-history-table thead { background: var(--aq-content-strong); color: var(--aq-secondary); }
.pager { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
@media (max-width: 760px) { .detail-heading { align-items: flex-start; flex-direction: column; }.detail-heading h1 { font-size: 24px; }.detail-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }.indicator-grid { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-transparency: reduce) { .detail-facts > div { background: var(--aq-content); } }
@media (forced-colors: active) { .detail-facts > div { border: 1px solid CanvasText; background: Canvas; color: CanvasText; }.quality-mark { border: 1px solid currentColor; } }
</style>
