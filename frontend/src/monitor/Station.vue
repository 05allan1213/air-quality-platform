<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Location, Star } from '@element-plus/icons-vue'
import { api, ApiError, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import { readFavorite, useMonitorPages } from './reads'

type StationObservation = { indicator: string; value: string | null; unit: string; quality: string; grade: number | null; standard_series: string; observed_at: string; fetched_at: string | null; age_seconds: number | null; freshness: string; revision: number; revision_no: number; source_status?: string }
type Epoch = { station: number; site_epoch: number; city: number | null; station_name: string | null; city_name: string | null; site_name: string | null; codes: string[]; period_start: string | null; period_end: string | null; status: string; coordinates: { longitude: string; latitude: string; crs: string } | null; link: string }
type StationResponse = { product: number; station: number; site_epoch?: number; source: string; synthetic: boolean; as_of: string; state: 'select_epoch' | 'ok' | 'no_data'; page: { page_size: number; next_cursor: string | null }; identity?: Epoch; source_status?: { state?: string; reason?: string }; history?: string[]; results?: StationObservation[] }
type EpochPage = { product: number; station: number; source: string; synthetic: boolean; as_of: string; state: string; results: Epoch[]; page: { page_size: number; next_cursor: string | null } }
type Favorite = { id: number; kind: string; target_id?: number; product?: number; state: string }
type DetailResponse = { state: string; results: StationObservation[]; page: { page_size: number; next_cursor: string | null } }

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const stationRead = usePrivateRead<StationResponse>()
const epochRead = useMonitorPages<EpochPage>()
const favoriteRead = usePrivateRead<Favorite | null>()
const historyRead = useMonitorPages<DetailResponse>()
const stationId = computed(() => String(route.params.id))
const product = computed(() => typeof route.query.product === 'string' ? route.query.product : '')
const siteEpoch = computed(() => typeof route.query.site_epoch === 'string' ? route.query.site_epoch : '')
const scope = computed(() => session.scope === 'synthetic' ? 'synthetic' : undefined)
const favoriteItem = computed(() => favoriteRead.data.value)
const busyFavorite = ref(false)
const favoriteMessage = ref('')
const historyOpen = ref(false)
const labels: Record<string, string> = { 'PM2.5': 'PM₂.₅', PM10: 'PM₁₀', SO2: 'SO₂', NO2: 'NO₂', CO: 'CO', O3: 'O₃' }

function requestParams() {
  const params: Record<string, string> = { product: product.value }
  if (scope.value) params.scope = scope.value
  for (const key of ['site_epoch', 'as_of'] as const) {
    const value = route.query[key]
    if (typeof value === 'string' && value) params[key] = value
  }
  return params
}
watch(() => [stationId.value, product.value, siteEpoch.value, route.query.as_of, session.scope], () => {
  historyOpen.value = false
  historyRead.clear()
  if (!product.value || !/^\d+$/.test(stationId.value)) {
    stationRead.clear(); epochRead.clear(); favoriteRead.clear(); return
  }
  const current = requestParams()
  if (siteEpoch.value) {
    epochRead.clear()
    void stationRead.run(signal => api.get<StationResponse>(`stations/${stationId.value}/`, current, signal))
  } else {
    stationRead.clear()
    epochRead.open({ path: `stations/${stationId.value}/`, params: current })
  }
  void favoriteRead.run(signal => readFavorite('station', stationId.value, product.value, scope.value, signal))
}, { immediate: true })

function selectEpoch(epoch: Epoch) {
  if (!epoch.link.startsWith('/api/v1/stations/')) return
  const url = new URL(epoch.link, window.location.origin)
  if (url.origin !== window.location.origin || !/^\/api\/v1\/stations\/\d+\/$/.test(url.pathname)) return
  const query = Object.fromEntries([...url.searchParams].filter(([key]) => ['product', 'site_epoch', 'as_of', 'scope'].includes(key)))
  void router.push({ name: 'station', params: { id: stationId.value }, query })
}
async function toggleFavorite() {
  if (busyFavorite.value || !product.value || !siteEpoch.value) return
  busyFavorite.value = true
  favoriteMessage.value = ''
  try {
    const query = scope.value ? `?scope=${scope.value}` : ''
    if (favoriteItem.value) await api.delete(`me/favorites/${favoriteItem.value.id}/${query}`)
    else await api.post(`me/favorites/${query}`, { kind: 'station', target_id: stationId.value, product: product.value })
    favoriteMessage.value = favoriteItem.value ? '关注状态已更新。' : '站点已加入我的关注。'
    void favoriteRead.run(signal => readFavorite('station', stationId.value, product.value, scope.value, signal))
  } catch (error) {
    favoriteMessage.value = error instanceof ApiError && error.code === 'favorite_exists' ? '此站点已在关注列表中。' : '关注操作未完成，请重新读取后再试。'
  } finally { busyFavorite.value = false }
}
async function loadHistory(href: string) {
  if (!href.startsWith('/api/v1/')) return
  const url = new URL(href, window.location.origin)
  if (!url.pathname.startsWith('/api/v1/observations/query/')) return
  historyOpen.value = true
  const params = Object.fromEntries(url.searchParams.entries())
  historyRead.open({ path: 'observations/query/', params })
}
function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'not_authenticated') return '登录状态已失效，私有记录已清除。请重新登录。'
    if (error.code === 'use_denied') return '该站点或站址期当前不可披露。'
    if (error.code === 'not_found') return '该站点当前不可访问。'
    if (error.code === 'invalid_request') return '站点筛选参数不受支持。'
  }
  return '站点观测暂时无法读取，请重试。'
}
const errorMessage = computed(() => stationRead.error.value?.message ?? epochRead.error.value?.message ?? '')
function qualityLabel(quality: string) {
  return ({ valid: '有效', suspect: '待核', missing: '缺测', censored: '删失', invalid: '无效', disputed: '争议' } as Record<string, string>)[quality] ?? '未知'
}
function freshnessLabel(freshness: string, age: number | null) {
  if (freshness === 'fresh') return '最新'
  if (freshness === 'stale' && age !== null) return `距观测 ${Math.floor(age / 3600)} 小时`
  return '时效未知'
}
</script>

<template>
  <div class="monitor-page station-page">
    <header class="page-heading station-heading">
      <div>
        <button class="text-action back-link" type="button" @click="router.back()"><ArrowLeft aria-hidden="true"/>返回上页</button>
        <h1>{{ stationRead.data.value?.identity?.station_name ?? epochRead.data.value?.results?.[0]?.station_name ?? `站点 ${stationId}` }}</h1>
        <p class="heading-copy">数值依具体站址期和指标时刻展示，站点名本身不代表跨时期身份连续。</p>
      </div>
      <div v-if="stationRead.data.value?.identity" class="heading-tools">
        <span class="status">{{ stationRead.data.value.synthetic ? 'F · 合成数据' : 'S/H · 站点观测' }}</span>
        <RouterLink class="btn" :to="{ name: 'station', params: { id: stationId }, query: { product, scope, as_of: route.query.as_of } }">切换站址期</RouterLink>
        <button class="btn" type="button" :disabled="busyFavorite" @click="toggleFavorite"><Star aria-hidden="true"/>{{ favoriteItem ? '取消关注' : '关注站点' }}</button>
      </div>
    </header>
    <p v-if="favoriteMessage" class="inline-message" role="status">{{ favoriteMessage }}</p>
    <p v-if="errorMessage" class="error-line" role="alert">{{ errorMessage }} <button v-if="product" class="text-action" type="button" @click="stationRead.run(signal => api.get<StationResponse>(`stations/${stationId}/`, requestParams(), signal))">重试</button></p>
    <section v-if="!product" class="panel empty-panel"><h2>需要从获准站点目录进入</h2><p>此页面不推断来源或产品。请从同城目录、产品目录或本人关注中的有效链接打开。</p></section>
    <section v-else-if="epochRead.data.value && !siteEpoch" class="panel epoch-panel" aria-labelledby="epoch-title"><div class="section-head"><div><h2 id="epoch-title">选择站址期</h2><p>{{ epochRead.data.value.source }} · 产品 {{ epochRead.data.value.product }} · {{ new Date(epochRead.data.value.as_of).toLocaleString('zh-CN') }}</p></div><span class="status">{{ epochRead.data.value.results.length }} 个获准站址期</span></div><div v-if="epochRead.data.value.results.length" class="epoch-list"><article v-for="epoch in epochRead.data.value.results" :key="epoch.site_epoch" class="epoch-row"><div class="epoch-main"><strong>{{ epoch.site_name ?? epoch.station_name ?? `站址期 ${epoch.site_epoch}` }}</strong><span>{{ epoch.city_name ?? '城市未披露' }} · {{ epoch.status === 'current' ? '当前站址期' : epoch.status === 'historical' ? '历史站址期' : '状态未知' }}</span><small>{{ epoch.period_start ? new Date(epoch.period_start).toLocaleString('zh-CN') : '起始时刻未知' }} – {{ epoch.period_end ? new Date(epoch.period_end).toLocaleString('zh-CN') : '当前/结束时刻未知' }}</small><small v-if="epoch.codes.length">来源代码：{{ epoch.codes.join('、') }}</small></div><span v-if="epoch.coordinates" class="coordinate"><Location aria-hidden="true"/>{{ epoch.coordinates.latitude }}, {{ epoch.coordinates.longitude }}<small>{{ epoch.coordinates.crs }}</small></span><span v-else class="coordinate unavailable">位置未披露</span><button class="text-action" type="button" @click="selectEpoch(epoch)">查看记录 <ArrowRight aria-hidden="true"/></button></article></div><p v-else class="quiet-note">当前授权时刻没有可披露站址期。</p><div class="pager"><button v-if="epochRead.hasCursor.value || epochRead.error.value" class="btn" type="button" :disabled="epochRead.busy.value" @click="epochRead.first">{{ epochRead.hasCursor.value ? '返回第一页' : '重新读取' }}</button><button v-if="epochRead.data.value.page.next_cursor" class="btn" type="button" :disabled="epochRead.busy.value" @click="epochRead.next">下一页站址期</button></div></section>
    <template v-else-if="stationRead.data.value?.identity">
      <section class="detail-facts" aria-label="站址期信息"><div><span>站址期 ID</span><strong>{{ stationRead.data.value.identity.site_epoch }}</strong></div><div><span>所属城市</span><strong>{{ stationRead.data.value.identity.city_name ?? '未披露' }}</strong></div><div><span>源代码</span><strong>{{ stationRead.data.value.identity.codes.length ? stationRead.data.value.identity.codes.join('、') : '未披露' }}</strong></div><div><span>站址状态</span><strong>{{ stationRead.data.value.identity.status === 'current' ? '当前' : stationRead.data.value.identity.status === 'historical' ? '历史' : '未知' }}</strong></div><div><span>站址有效期</span><strong>{{ stationRead.data.value.identity.period_start ? new Date(stationRead.data.value.identity.period_start).toLocaleString('zh-CN') : '起点未知' }} – {{ stationRead.data.value.identity.period_end ? new Date(stationRead.data.value.identity.period_end).toLocaleString('zh-CN') : '开放或未知' }}</strong></div><div><span>坐标依据</span><strong>{{ stationRead.data.value.identity.coordinates?.crs ?? '坐标未披露' }}</strong></div></section>
      <section class="panel station-data-panel"><div class="section-head"><div><h2>最近有效观测</h2><p>各指标保留其自身观测时间、采集时间与质量状态</p></div><span class="status">{{ stationRead.data.value.source }} · 产品 {{ stationRead.data.value.product }}</span></div><div v-if="stationRead.data.value.results?.length" class="table-scroll" tabindex="0" aria-label="站点最近观测，可横向滚动"><table class="station-table"><thead><tr><th scope="col">指标</th><th scope="col">数值</th><th scope="col">质量</th><th scope="col">观测时刻</th><th scope="col">采集时刻</th><th scope="col">时效</th><th scope="col">标准系列</th><th scope="col">修订</th></tr></thead><tbody><tr v-for="item in stationRead.data.value.results" :key="`${item.indicator}-${item.standard_series}-${item.revision}`"><th scope="row">{{ labels[item.indicator] ?? item.indicator }}</th><td class="numeric">{{ item.value ?? (item.quality === 'missing' ? '缺测' : '未知') }} {{ item.unit }}</td><td><span class="quality-mark" :data-quality="item.quality">{{ qualityLabel(item.quality) }}</span></td><td>{{ new Date(item.observed_at).toLocaleString('zh-CN') }}</td><td>{{ item.fetched_at ? new Date(item.fetched_at).toLocaleString('zh-CN') : '未知' }}</td><td>{{ freshnessLabel(item.freshness, item.age_seconds) }}</td><td>{{ item.standard_series }}</td><td>{{ item.revision_no }}</td></tr></tbody></table></div><p v-else class="quiet-note">{{ stationRead.data.value.state === 'no_data' ? '该站址期在当前时刻没有可显示的观测。' : '没有符合条件的获准指标。' }}</p><div v-if="stationRead.data.value.identity.coordinates" class="coordinate-note"><Location aria-hidden="true"/><span>获准坐标 {{ stationRead.data.value.identity.coordinates.latitude }}, {{ stationRead.data.value.identity.coordinates.longitude }} · {{ stationRead.data.value.identity.coordinates.crs }}。未加载正式地图底图。</span></div><p v-else class="coordinate-note">正式地图及坐标投影未开放，站址身份和记录仍可查看。</p></section>
      <section class="panel history-panel" aria-labelledby="station-history-title"><div class="section-head"><div><h2 id="station-history-title">站址期历史窗口</h2><p>仅按后端提供的产品与授权时间范围读取</p></div></div><div v-if="stationRead.data.value.history?.length" class="history-buttons"><button v-for="(href, index) in stationRead.data.value.history" :key="href" class="btn" type="button" @click="loadHistory(href)">历史窗口 {{ index + 1 }}</button></div><p v-else class="quiet-note">当前没有可披露的历史窗口。</p><div v-if="historyOpen && historyRead.busy.value" class="quiet-note" role="status">正在读取历史窗口…</div><p v-if="historyRead.error.value" class="error-line" role="alert">历史窗口不可用：{{ errorText(historyRead.error.value) }}</p><div v-if="historyRead.data.value" class="table-scroll history-table" tabindex="0"><table><thead><tr><th scope="col">指标</th><th scope="col">观测时刻</th><th scope="col">数值</th><th scope="col">质量</th></tr></thead><tbody><tr v-for="item in historyRead.data.value.results" :key="`${item.observed_at}-${item.indicator}-${item.revision}`"><th scope="row">{{ labels[item.indicator] ?? item.indicator }}</th><td>{{ new Date(item.observed_at).toLocaleString('zh-CN') }}</td><td>{{ item.value ?? '无值' }} {{ item.unit }}</td><td>{{ qualityLabel(item.quality) }}</td></tr><tr v-if="!historyRead.data.value.results.length"><td colspan="4">此窗口没有可显示记录。</td></tr></tbody></table></div><div class="pager"><button v-if="historyRead.hasCursor.value || historyRead.error.value" class="btn" type="button" :disabled="historyRead.busy.value" @click="historyRead.first">{{ historyRead.hasCursor.value ? '返回历史第一页' : '重新读取历史' }}</button><button v-if="historyRead.data.value?.page.next_cursor" class="btn" type="button" :disabled="historyRead.busy.value" @click="historyRead.next">下一页历史</button></div></section>
    </template>
    <section v-else-if="stationRead.busy.value" class="panel loading-panel" role="status">正在读取站址期观测…</section>
  </div>
</template>

<style scoped>
.station-page { display: grid; grid-template-columns: minmax(0, 1fr); width: 100%; min-width: 0; gap: 14px; max-width: 1320px; margin: 0 auto; }
.station-page .panel { min-width: 0; padding: clamp(12px, 1.5vw, 18px); }
.station-heading { display: flex; justify-content: space-between; align-items: end; gap: 18px; }
.station-heading h1 { margin: 2px 0 0; font-size: 30px; }
.back-link { width: fit-content; white-space: nowrap; }
.heading-copy { margin: 7px 0 0; color: var(--aq-secondary); font-size: 13px; line-height: 1.55; }
.heading-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
.heading-tools .btn { flex: 0 0 auto; white-space: nowrap; }
.text-action { display: inline-flex; min-height: 32px; align-items: center; gap: 5px; padding: 4px 2px; border: 0; color: var(--aq-link); background: transparent; font-size: 13px; font-weight: 620; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.detail-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; overflow: hidden; border-radius: 8px; background: var(--aq-border); }
.detail-facts > div { display: grid; gap: 5px; min-width: 0; padding: 12px 14px; background: var(--aq-content); }
.detail-facts span { color: var(--aq-muted); font-size: 11px; }
.detail-facts strong { color: var(--aq-text); font-size: 13px; font-weight: 620; overflow-wrap: anywhere; }
.station-table { width: 100%; min-width: 920px; margin-top: 13px; border-collapse: collapse; font-size: 12px; }
.table-scroll { max-width: 100%; overflow: auto; border-radius: 7px; }
.station-table th, .station-table td { padding: 9px 10px; border-bottom: 1px solid var(--aq-border); text-align: left; white-space: nowrap; }
.station-table thead { background: var(--aq-content-strong); color: var(--aq-secondary); font-size: 11px; }
.station-table tbody th { font-weight: 620; }
.numeric { font-variant-numeric: tabular-nums; }
.quality-mark { padding: 2px 6px; border-radius: 4px; background: var(--aq-success-bg); color: var(--aq-success); font-size: 10px; }
.quality-mark[data-quality="missing"], .quality-mark[data-quality="invalid"] { background: var(--aq-warning-bg); color: var(--aq-warning); }
.quality-mark[data-quality="disputed"], .quality-mark[data-quality="suspect"] { background: var(--aq-danger-bg); color: var(--aq-danger); }
.quiet-note { color: var(--aq-secondary); font-size: 12px; line-height: 1.55; }
.coordinate-note { display: flex; align-items: flex-start; gap: 8px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--aq-border); color: var(--aq-secondary); font-size: 12px; line-height: 1.5; }
.coordinate { display: grid; grid-template-columns: 16px auto; align-items: center; gap: 2px 5px; color: var(--aq-secondary); font-size: 11px; }
.coordinate small { grid-column: 2; color: var(--aq-muted); }
.coordinate.unavailable { display: block; }
.epoch-list { display: grid; margin-top: 10px; border-top: 1px solid var(--aq-border); }
.epoch-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(150px, auto) auto; align-items: center; gap: 12px; padding: 12px 4px; border-bottom: 1px solid var(--aq-border); }
.epoch-main { display: grid; gap: 4px; }
.epoch-main strong { color: var(--aq-text); font-size: 14px; }
.epoch-main span, .epoch-main small { color: var(--aq-secondary); font-size: 12px; }
.epoch-main small { font-size: 11px; }
.history-buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.history-table { max-height: 320px; margin-top: 10px; }
.history-table table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 12px; }
.history-table th, .history-table td { padding: 8px 10px; border-bottom: 1px solid var(--aq-border); text-align: left; white-space: nowrap; }
.history-table thead { background: var(--aq-content-strong); color: var(--aq-secondary); }
.error-line { color: var(--aq-danger); font-size: 13px; }
.inline-message { color: var(--aq-success); font-size: 12px; }
.pager { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
@media (max-width: 760px) { .station-heading { align-items: flex-start; flex-direction: column; }.station-heading h1 { font-size: 24px; }.detail-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }.epoch-row { grid-template-columns: minmax(0, 1fr); align-items: start; }.station-page .section-head { align-items: start; flex-wrap: wrap; gap: 8px; } }
@media (prefers-reduced-transparency: reduce) { .detail-facts > div { background: var(--aq-content); } }
@media (forced-colors: active) { .detail-facts > div { border: 1px solid CanvasText; background: Canvas; color: CanvasText; }.quality-mark { border: 1px solid currentColor; } }
</style>
