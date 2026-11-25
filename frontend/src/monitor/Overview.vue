<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { ArrowLeft, ArrowRight, Location, Refresh, Search } from '@element-plus/icons-vue'
import { api, ApiError, privateStateGeneration, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import HomeRatios from './HomeRatios.vue'
import OverviewTrend from './OverviewTrend.vue'
import { cityLabel, gradeLabel, gradeLabels, homeCityLink, knownStandards, rememberHomeReturn, standardLabel, takeHomeReturn, utcLabel, type HomeCity, type NationalResponse } from './HomeProjection'
import type { PageInfo } from './reads'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const request = usePrivateRead<NationalResponse>()
const returning = takeHomeReturn(route.fullPath, privateStateGeneration(), router.options.history.state.position)
const cursors = ref<string[]>(returning?.cursors ?? [])
const cursor = computed(() => cursors.value.at(-1))
const cityHeading = ref<HTMLHeadingElement>()
const tableHeading = ref<HTMLHeadingElement>()
const tablePanel = ref<HTMLElement>()
const term = (key: string) => typeof route.query[key] === 'string' ? route.query[key] : undefined
const searchDraft = ref(term('q') ?? '')
const geographyDraft = ref(term('geography') ?? '')
const focusCity = computed(() => term('focus_city'))
const selecting = ref<string>()
const focusNotice = ref(false)
const selected = computed(() => {
  const row = request.data.value?.focused
  return row && String(row.city) === (selecting.value ?? focusCity.value) ? row : null
})
const rows = computed(() => request.data.value?.results ?? [])
const summary = computed(() => request.data.value?.summary)
const groups = computed(() => request.data.value?.summary.groups ?? {})
const standard = computed(() => term('standard_series') ?? '')
const standards = computed(() => [...new Set([...knownStandards, ...(standard.value ? [standard.value] : [])])])
const hasKnownStandard = computed(() => knownStandards.includes(standard.value))
const grade = computed(() => term('grade') ?? '')
const order = computed(() => term('order') ?? 'city')
const rankOrder = computed(() => term('rank_preview') ?? 'aqi_desc')
const selectedScope = computed(() => session.scope === 'synthetic' ? 'synthetic' : undefined)
const filtering = computed(() => Boolean(term('q') || term('geography') || grade.value || standard.value || order.value !== 'city'))
const chosenStandardGroup = computed(() => groups.value[standard.value])
const coverageNote = computed(() => {
  if (request.busy.value && !summary.value) return '正在读取摘要'
  if (summary.value?.state === 'unavailable') return '当前用途不可披露覆盖'
  if (summary.value?.expected === 0) return '名录无应到城市'
  if (summary.value?.completeness === 'complete') return '名录完整到报'
  if (summary.value?.completeness === 'partial') return '名录部分到报'
  if (grade.value) return '按等级筛选时应到未知'
  if (summary.value?.covered_city_count == null) return '覆盖数与应到数未知'
  return '应到数未知'
})
const rankingNote = computed(() => {
  if (request.busy.value && !summary.value) return '正在读取摘要'
  if (summary.value?.state === 'unavailable') return '当前用途不可披露汇总'
  if (!hasKnownStandard.value) return '选择已核标准查看占比'
  if (summary.value?.summary_eligible_city_count === 0) return '无可汇总城市'
  if (!chosenStandardGroup.value) return '可排名城市数未知'
  return standard.value
})
const anchor = computed(() => request.data.value?.snapshot_at ?? term('at'))
const selectedValues = computed(() => selected.value?.series.filter(item => item.indicator === 'AQI') ?? [])
const selectionKey = computed(() => selected.value && request.data.value
  ? `${request.data.value.selection.source}/${request.data.value.selection.product}/${request.data.value.snapshot_at}/${selected.value.city}` : '')
const trendTarget = computed(() => selected.value ? homeCityLink(selected.value, 'trend', selectedScope.value, anchor.value) : null)
const distributionRows = computed(() => chosenStandardGroup.value
  ? gradeLabels.map((label, index) => ({ label, count: chosenStandardGroup.value?.distribution[String(index + 1)] ?? 0, grade: index + 1 }))
  : [])
const gradeBounds = computed(() => [
  standard.value === 'HJ633-2026' ? '1–50' : standard.value === 'HJ633-2012' ? '0–50' : '≤ 50（有效值下限按所用标准）',
  '51–100', '101–150', '151–200', '201–300', standard.value === 'HJ633-2026' ? '301–500' : '> 300',
])
let pendingAdvance: string | undefined
let removedFocus: string | undefined
let selectionTrigger: { city: number; entrance: 'rank' | 'table' } | undefined
let focusSelectedHeading = false
let departing = false
let readSequence = 0

const tableParams = computed(() => {
  const params: Record<string, string | number> = { home: 1, map_limit: 400, page_size: 25 }
  if (selectedScope.value) params.scope = selectedScope.value
  for (const key of ['product', 'at', 'as_of', 'standard_series', 'window_definition', 'q', 'geography', 'grade', 'order']) {
    const value = term(key)
    if (value) params[key] = value
  }
  return params
})
const tableKey = computed(() => JSON.stringify(tableParams.value))

async function refresh() {
  const sequence = ++readSequence
  const params = { ...tableParams.value }
  if (hasKnownStandard.value) params.rank_preview = rankOrder.value
  if (focusCity.value) params.focus_city = focusCity.value
  if (cursor.value) params.cursor = cursor.value
  const result = await request.run(async signal => {
    if (params.scope === 'synthetic' && !params.product) {
      let catalogCursor: string | null = null
      do {
        const page: { results: { product: number; shape: string }[]; page: PageInfo } = await api.get('catalog/objects/', { scope: params.scope, page_size: 100, cursor: catalogCursor }, signal)
        const item = page.results.find(item => item.shape === 'L')
        if (item) { params.product = item.product; break }
        catalogCursor = page.page.next_cursor
      } while (catalogCursor)
      if (!params.product) throw new ApiError('当前数据域没有获准的全国 L 快照。', 404, 'no_catalog')
    }
    return api.get<NationalResponse>('overview/national/', params, signal)
  })
  if (sequence !== readSequence || departing || route.name !== 'overview' || params.focus_city !== focusCity.value) return undefined
  if (params.focus_city && ((result && !result.focused) || ['use_denied', 'permission_denied'].includes(request.error.value?.code ?? ''))) {
    focusNotice.value = true
    const query = { ...route.query, focus_city: undefined }
    removedFocus = router.resolve({ name: 'overview', query }).fullPath
    void router.replace({ name: 'overview', query })
  }
  return result
}

function pinnedQuery(changes: LocationQueryRaw = {}) {
  const current = request.data.value
  return {
    ...route.query,
    ...(current?.snapshot_at ? { at: current.snapshot_at, as_of: undefined, product: String(current.selection.product) } : {}),
    ...changes, scope: selectedScope.value, cursor: undefined,
  }
}

watch(() => term('q'), value => { searchDraft.value = value ?? '' })
watch(() => term('geography'), value => { geographyDraft.value = value ?? '' })
watch([tableKey, focusCity, rankOrder], async ([key], previous) => {
  if (departing || route.name !== 'overview') return
  if (removedFocus && removedFocus === route.fullPath) { removedFocus = undefined; return }
  removedFocus = undefined
  const advance = pendingAdvance === route.fullPath
  pendingAdvance = undefined
  if (previous?.[0] && key !== previous[0]) cursors.value = []
  focusNotice.value = false
  const result = await refresh()
  if (advance && result?.page.next_cursor && key === tableKey.value) await goToPage([result.page.next_cursor])
}, { immediate: true })

function clearLocalPage() {
  readSequence += 1
  cursors.value = []
  pendingAdvance = undefined
  selectionTrigger = undefined
  focusSelectedHeading = false
  selecting.value = undefined
  focusNotice.value = false
}
window.addEventListener('air-quality:clear-reads', clearLocalPage)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', clearLocalPage))

function applyFilter(changes: LocationQueryRaw) {
  return router.replace({ name: 'overview', query: pinnedQuery(changes) })
}
function clearFilters() {
  searchDraft.value = ''
  geographyDraft.value = ''
  return applyFilter({ q: undefined, geography: undefined, grade: undefined, standard_series: undefined, order: undefined, rank_preview: undefined,
    ...(request.error.value?.code === 'invalid_request' ? { focus_city: undefined } : {}),
  })
}
const cityLink = (row: HomeCity) => homeCityLink(row, 'city', selectedScope.value, anchor.value)

async function openCity(row: HomeCity, entrance: 'detail' | 'table') {
  const target = cityLink(row)
  if (!target || departing) return
  departing = true
  const generation = privateStateGeneration()
  const scroll = window.scrollY
  try {
    await applyFilter({ focus_city: String(row.city) })
    if (generation !== privateStateGeneration() || route.name !== 'overview') return
    rememberHomeReturn({ path: route.fullPath, generation, position: router.options.history.state.position, cursors: [...cursors.value], city: row.city, entrance, scroll })
    const failure = await router.push({ name: 'city', params: { id: row.city }, query: target.params })
    if (failure) rememberHomeReturn()
  } finally {
    departing = false
  }
}
async function setFocus(row: { city: number }, entrance: 'rank' | 'table') {
  selectionTrigger = { city: row.city, entrance }
  const id = String(row.city)
  if (focusCity.value !== id) {
    focusSelectedHeading = true
    selecting.value = id
    await applyFilter({ focus_city: id })
    if (selecting.value === id) selecting.value = undefined
  } else cityHeading.value?.focus()
}
watch(selectionKey, async value => {
  if (!value || !focusSelectedHeading || selected.value?.city !== selectionTrigger?.city) return
  focusSelectedHeading = false
  await nextTick()
  cityHeading.value?.focus()
})
async function closeFocus() {
  focusSelectedHeading = false
  selecting.value = ''
  await applyFilter({ focus_city: undefined })
  selecting.value = undefined
  const trigger = selectionTrigger
  selectionTrigger = undefined
  if (!trigger) { tableHeading.value?.focus(); return }
  const stop = watch(() => request.busy.value, async busy => {
    if (busy) return
    stop()
    await nextTick()
    const control = window.document.getElementById(`home-${trigger.entrance}-${trigger.city}`)
    ;(control ?? tableHeading.value)?.focus()
  })
  if (!request.busy.value) { stop(); tableHeading.value?.focus() }
}

async function goToPage(stack: string[]) {
  cursors.value = stack
  await refresh()
  await nextTick()
  tableHeading.value?.focus()
}
async function nextPage() {
  const next = request.data.value?.page.next_cursor
  if (!next || request.busy.value) return
  const query = pinnedQuery()
  const pinned = router.resolve({ name: 'overview', query }).fullPath
  if (pinned !== route.fullPath) {
    // The latest-query cursor is signed for its original conditions. Get an exact first-page cursor before advancing.
    pendingAdvance = pinned
    if (await router.replace({ name: 'overview', query })) pendingAdvance = undefined
    return
  }
  await goToPage([...cursors.value, next])
}
const firstPage = () => goToPage([])
const previousPage = () => goToPage(cursors.value.slice(0, -1))
const tableReadings = (row: HomeCity) => row.series.filter(item => item.indicator === 'AQI')
const snapshotLabel = computed(() => anchor.value ? utcLabel(anchor.value) : '尚无共同观测时刻')

if (returning) {
  const stop = watch(() => request.busy.value, async busy => {
    if (busy) return
    stop()
    await nextTick()
    window.scrollTo(0, returning.scroll)
    const control = returning.entrance === 'detail' ? document.getElementById('home-selected-city-link')
      : tablePanel.value?.querySelector<HTMLElement>(`[data-city-detail="${returning.city}"]`)
    ;(control ?? tableHeading.value)?.focus({ preventScroll: true })
  })
}

function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'not_authenticated') return '登录状态已失效，请重新登录。'
    if (error.code === 'page_changed') return '当前快照已变化，请重新读取第一页。'
    if (error.code === 'use_denied') return '当前筛选的观测不在可用范围内。'
    if (error.code === 'permission_denied') return '当前账号没有读取此数据的权限。'
    if (error.code === 'invalid_request') return '筛选条件不受支持，请调整条件后重试。'
    if (error.code === 'no_catalog') return error.message
  }
  return '全国观测暂时无法读取，请检查条件后重试。'
}
const errorMessage = computed(() => request.error.value ? errorText(request.error.value) : '')
const rankingRows = computed(() => request.data.value?.ranking?.rows ?? [])
</script>

<template>
  <div class="monitor-page overview-page">
    <header class="page-heading overview-heading">
      <div>
        <h1>全国空气质量总览</h1>
        <p class="heading-copy">L 全国城市列表 · {{ request.data.value?.selection.source ?? '来源待确认' }}<template v-if="request.data.value"> · 产品 {{ request.data.value.selection.product }}</template></p>
      </div>
      <div class="heading-meta">
        <span class="status" :data-kind="request.data.value?.synthetic ? 'synthetic' : 'formal'">
          {{ request.data.value?.synthetic ? 'F · 合成数据' : 'L · 全国城市列表' }}
        </span>
        <time v-if="anchor" class="snapshot-time" :datetime="anchor">{{ request.data.value?.state === 'ok' ? '观测' : '锚点' }} {{ snapshotLabel }}</time>
        <span v-else class="snapshot-time">{{ request.busy.value ? '正在确认观测时刻' : snapshotLabel }}</span>
        <button class="btn icon-action" type="button" aria-label="重新读取全国观测" :disabled="request.busy.value" @click="refresh"><Refresh aria-hidden="true"/></button>
      </div>
    </header>

    <section class="summary-strip" aria-label="全国观测摘要">
      <HomeRatios label="同刻覆盖 / 应到" :numerator="summary?.covered_city_count" :denominator="summary?.expected" :note="coverageNote" />
      <HomeRatios label="可排名 / 可汇总" :numerator="chosenStandardGroup?.ranked_city_count" :denominator="summary?.summary_eligible_city_count" :note="rankingNote" />
      <div><span>本页城市</span><strong>{{ request.data.value ? rows.length : '—' }}</strong><small>按获准范围及已应用条件</small></div>
      <div><span>正式地图</span><strong class="blocked-value">未开放</strong><small>使用同条件城市列表</small></div>
    </section>

    <section class="overview-grid">
      <section class="panel coverage-panel" aria-labelledby="coverage-title">
        <div class="section-head">
          <div><h2 id="coverage-title">同刻城市分布</h2><p>{{ snapshotLabel }} · {{ standard ? standardLabel(standard) : '标准分开展示' }}</p></div>
          <span class="map-status">地图未开放</span>
        </div>
        <div class="map-unavailable" role="status">
          <Location class="map-icon" aria-hidden="true"/>
          <div><strong>当前以城市列表定位观测</strong><p>正式地图需要已核验的几何、坐标系与使用授权。下面的筛选结果与城市下钻不依赖地图资产。</p></div>
        </div>
        <div class="distribution-head"><h3>AQI 等级分布</h3><label class="compact-field"><span>标准</span><select :value="standard" aria-label="AQI 标准系列" @change="applyFilter({ standard_series: ($event.target as HTMLSelectElement).value || undefined, grade: undefined, order: 'city', rank_preview: undefined })"><option value="">全部可披露标准</option><option v-for="item in standards" :key="item" :value="item">{{ standardLabel(item) }}</option></select></label></div>
        <p v-if="summary?.state === 'unavailable'" class="quiet-note">当前范围不提供分布或排行；获准城市仍可在普通列表查看。</p>
        <p v-else-if="!standard && Object.keys(groups).length" class="quiet-note">分别可汇总：{{ Object.entries(groups).map(([item, group]) => `${item}（${group.ranked_city_count} 城）`).join('；') }}。选择一个标准查看分布，不合并分母。</p>
        <p v-else-if="!chosenStandardGroup" class="quiet-note">{{ request.busy.value ? '正在读取分布…' : '当前没有可显示的标准分组。' }}</p>
        <p v-if="chosenStandardGroup" class="quiet-note" data-testid="distribution-base">分布分母：{{ standard }} 下符合条件且可排名的 {{ chosenStandardGroup.ranked_city_count }} 城。</p>
        <div v-if="chosenStandardGroup" class="grade-list">
          <button v-for="item in distributionRows" :key="item.grade" type="button" class="grade-row" :aria-pressed="grade === String(item.grade)" @click="applyFilter({ grade: grade === String(item.grade) ? undefined : String(item.grade), order: 'city' })">
            <span class="grade-swatch" :data-grade="item.grade" aria-hidden="true"></span><span>{{ item.label }}</span><strong>{{ item.count }}</strong><small>城</small>
          </button>
        </div>
        <details class="grade-legend">
          <summary>AQI 六级区间说明</summary>
          <dl>
            <div v-for="(label, index) in gradeLabels" :key="label"><dt>{{ index + 1 }} · {{ label }}</dt><dd>{{ gradeBounds[index] }}</dd></div>
          </dl>
          <p>等级仅采用后端返回的已核标准判定。L 污染物等级不是浓度。</p>
        </details>
        <button type="button" class="text-action" @click="tableHeading?.focus()">跳到可搜索城市列表 <ArrowRight aria-hidden="true" /></button>
      </section>

      <section class="panel ranking-panel" aria-labelledby="ranking-title">
        <div class="section-head"><div><h2 id="ranking-title">城市 AQI 排名</h2><p>榜单与表格采用同一 L 快照</p></div><span class="top-five">TOP 5</span></div>
        <div class="segmented rank-switch" role="group" aria-label="排名方向">
          <button type="button" :disabled="!hasKnownStandard" :aria-pressed="rankOrder === 'aqi_desc'" @click="applyFilter({ rank_preview: 'aqi_desc' })">AQI 较高</button>
          <button type="button" :disabled="!hasKnownStandard" :aria-pressed="rankOrder === 'aqi_asc'" @click="applyFilter({ rank_preview: 'aqi_asc' })">AQI 较低</button>
        </div>
        <ol v-if="rankingRows.length" class="ranking-list">
          <li v-for="row in rankingRows" :key="row.city"><button :id="`home-rank-${row.city}`" type="button" class="ranking-row" :aria-current="selected?.city === row.city ? 'true' : undefined" @click="setFocus(row, 'rank')">
            <span class="rank-index">{{ String(row.rank).padStart(2, '0') }}</span><span class="rank-name">{{ cityLabel(row) }}<small>{{ row.geography ?? '地域未披露' }}</small></span><strong class="aqi-value">{{ row.aqi }}</strong><span class="grade-chip" :data-grade="row.grade">{{ gradeLabel(row.grade) }}</span>
          </button></li>
        </ol>
        <p v-else class="quiet-note">{{ !hasKnownStandard ? '选择已核标准后查看榜单，不跨标准混排。' : request.busy.value ? '正在读取城市榜单…' : request.data.value?.ranking?.state === 'ok' ? '当前条件下没有可排名的城市。' : '当前条件或授权下排名不可用。' }}</p>
      </section>
    </section>

    <p v-if="focusNotice" class="panel quiet-note selection-notice" role="status">所选城市已不符合当前条件或无法显示。</p>
    <section v-if="selected && request.data.value" id="home-selection" class="panel selected-panel" aria-labelledby="selected-city-title" :data-city="selected.city">
      <div class="section-head">
        <div><p>选中城市 · L 同刻观测</p><h2 id="selected-city-title" ref="cityHeading" tabindex="-1">{{ cityLabel(selected) }}</h2><p>{{ selected.geography ?? '地域未披露' }} · {{ snapshotLabel }}</p></div>
        <button class="btn" type="button" @click="closeFocus">关闭城市概览</button>
      </div>
      <div class="selected-layout">
        <div class="selected-l">
          <dl v-if="selectedValues.length" class="selected-readings">
            <div v-for="item in selectedValues" :key="item.revision">
              <dt><strong>AQI {{ item.value ?? '无可披露值' }}</strong> <span class="grade-chip" :data-grade="item.grade ?? 0">{{ gradeLabel(item.grade) }}</span></dt>
              <dd>{{ standardLabel(item.standard_series) }} · 首要污染物 {{ item.primary_pollutant ?? '未披露' }}</dd>
              <dd v-if="item.standard_series === 'unknown' && item.source_description">来源描述：{{ item.source_description }}</dd>
              <dd>来源时区 {{ item.source_timezone ?? '未知' }}<template v-if="item.attribution"> · {{ item.attribution }}</template></dd>
              <dd v-if="item.freshness === 'unknown'">来源时效未知</dd>
            </div>
          </dl>
          <p v-else class="quiet-note">无可披露的 L AQI 读数。</p>
          <details v-if="selected.levels.length" class="grade-legend">
            <summary>L 污染物等级（非浓度）</summary>
            <dl><div v-for="item in selected.levels" :key="item.revision"><dt>{{ item.indicator.replace(/Level$/, '') }}</dt><dd>{{ item.value ?? '未披露' }}</dd></div></dl>
          </details>
          <button v-if="cityLink(selected)" id="home-selected-city-link" class="text-action" type="button" @click="openCity(selected, 'detail')">查看选中城市 <ArrowRight aria-hidden="true" /></button>
        </div>
        <OverviewTrend :key="selectionKey" :target="trendTarget" :city="selected.city" :label="cityLabel(selected)" :source="request.data.value.selection.source" :synthetic="request.data.value.synthetic" />
      </div>
    </section>

    <section ref="tablePanel" class="panel city-table-panel" aria-labelledby="city-table-title" :aria-busy="request.busy.value">
      <div class="section-head table-title-row"><div><h2 id="city-table-title" ref="tableHeading" tabindex="-1">城市空气质量列表</h2><p>地点、AQI 及等级取自同一 L 快照。</p></div><button v-if="filtering || (errorMessage && focusCity)" class="btn" type="button" @click="clearFilters">清除筛选</button></div>
      <form class="filter-bar" @submit.prevent="applyFilter({ q: searchDraft.trim() || undefined, geography: geographyDraft.trim() || undefined })">
        <label class="field search-field"><span>城市检索</span><span class="search-control"><Search aria-hidden="true"/><input v-model="searchDraft" type="search" placeholder="城市名称 / 检索别名" autocomplete="off"/></span></label>
        <label class="field"><span>地域</span><input v-model="geographyDraft" type="search" placeholder="省份或地域"/></label>
        <button class="btn filter-apply" type="submit">应用检索</button>
        <div class="field"><label for="home-grade">AQI 等级</label><select id="home-grade" :value="grade" :disabled="!hasKnownStandard" @change="applyFilter({ grade: ($event.target as HTMLSelectElement).value || undefined })"><option value="">全部等级</option><option v-for="item in 6" :key="item" :value="item">{{ item }} · {{ gradeLabels[item - 1] }}</option></select></div>
        <label class="field"><span>表格排序</span><select :value="order" @change="applyFilter({ order: ($event.target as HTMLSelectElement).value })"><option value="city">城市顺序</option><option value="aqi_desc" :disabled="!hasKnownStandard">AQI 从高到低</option><option value="aqi_asc" :disabled="!hasKnownStandard">AQI 从低到高</option></select></label>
      </form>
      <p v-if="filtering" class="quiet-note applied-filters">已应用：城市 {{ term('q') ?? '不限' }} · 地域 {{ term('geography') ?? '不限' }} · 标准 {{ standard ? standardLabel(standard) : '不限' }} · 等级 {{ grade ? gradeLabel(Number(grade)) : '不限' }} · {{ order === 'aqi_desc' ? 'AQI 降序' : order === 'aqi_asc' ? 'AQI 升序' : '城市顺序' }}</p>
      <p v-if="errorMessage" class="error-line" role="alert">{{ errorMessage }} <button class="text-action" type="button" @click="cursor ? firstPage() : refresh()">{{ cursor ? '返回第一页' : '重试' }}</button></p>
      <div v-else-if="request.busy.value && !request.data.value" class="loading-line" role="status">正在读取当前授权下的全国快照…</div>
      <div v-else class="table-scroll" role="region" tabindex="0" aria-label="城市空气质量表，可横向滚动">
        <table class="observation-table" aria-labelledby="city-table-title"><thead><tr><th scope="col">城市</th><th scope="col">地域</th><th scope="col" :aria-sort="order === 'aqi_desc' ? 'descending' : order === 'aqi_asc' ? 'ascending' : undefined">AQI</th><th scope="col">等级</th><th scope="col">首要污染物</th><th scope="col">标准</th><th scope="col">详情</th></tr></thead><tbody>
          <tr v-for="row in rows" :key="row.city" :class="{ selected: selected?.city === row.city }">
            <th scope="row"><button :id="`home-table-${row.city}`" class="table-city-link" type="button" :aria-pressed="selected?.city === row.city" @click="setFocus(row, 'table')">{{ cityLabel(row) }}</button></th>
            <td>{{ row.geography ?? '未披露' }}</td>
            <td class="numeric"><span v-for="item in tableReadings(row)" :key="item.revision" class="table-series">{{ item.value ?? '未披露' }}</span></td>
            <td><span v-for="item in tableReadings(row)" :key="item.revision" class="table-series"><span class="grade-chip" :data-grade="item.grade ?? 0">{{ gradeLabel(item.grade) }}</span></span></td>
            <td><span v-for="item in tableReadings(row)" :key="item.revision" class="table-series">{{ item.primary_pollutant ?? '未披露' }}</span></td>
            <td><span v-for="item in tableReadings(row)" :key="item.revision" class="table-series">{{ standardLabel(item.standard_series) }}</span></td>
            <td><button class="text-action" type="button" :data-city-detail="row.city" :disabled="!cityLink(row)" @click="openCity(row, 'table')">城市记录 <ArrowRight aria-hidden="true"/></button></td>
          </tr>
          <tr v-if="!rows.length && !request.busy.value"><td colspan="7" class="empty-cell">{{ request.data.value?.state === 'no_data' ? '此筛选条件下没有可显示的城市观测。' : '尚无可显示的获准记录。' }}</td></tr>
        </tbody></table>
      </div>
      <footer class="table-footer"><span role="status">第 {{ cursors.length + 1 }} 页<span v-if="request.data.value"> · {{ rows.length }} 城</span></span><div class="page-controls"><button v-if="cursor" class="btn" type="button" :disabled="request.busy.value || !request.data.value" @click="previousPage"><ArrowLeft aria-hidden="true" />上一页</button><button class="btn" type="button" :disabled="!request.data.value?.page.next_cursor || request.busy.value" @click="nextPage">下一页 <ArrowRight aria-hidden="true"/></button></div></footer>
    </section>
  </div>
</template>

<style scoped>
.overview-page { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0; max-width: 1680px; margin: 0 auto; }
.overview-heading { display: flex; justify-content: space-between; align-items: end; gap: 20px; padding: 4px 2px 2px; }
.page-heading h1 { margin: 0; font-size: 30px; line-height: 1.25; font-weight: 720; color: var(--aq-text-primary, #142f47); }
.heading-copy, .section-head p { margin: 6px 0 0; color: var(--aq-secondary); font-size: 13px; overflow-wrap: anywhere; }
.heading-meta { display: flex; flex-wrap: wrap; justify-content: end; align-items: center; gap: 8px; color: var(--aq-text-secondary, #405f75); font-size: 13px; }
.status, .snapshot-time, .map-status, .top-five { padding: 6px 10px; border-radius: 999px; background: #edf7fc; color: #214b67; }
.status[data-kind="synthetic"] { background: #eff7fc; color: #21465e; }
.icon-action { display: inline-grid; place-items: center; width: 36px; height: 36px; padding: 0; }
.summary-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; border-radius: 12px; overflow: hidden; background: #bed7e8; box-shadow: 0 2px 8px #335f7d1a; }
.summary-strip > div { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 4px 12px; min-width: 0; padding: 12px 16px; background: #f4f9fc; }
.summary-strip span { font-size: 12px; color: #405f75; }
.summary-strip strong { grid-row: span 2; font-size: 23px; font-weight: 680; color: #173850; font-variant-numeric: tabular-nums; }
.summary-strip small { font-size: 11px; color: #48667a; }
.summary-strip .blocked-value { color: #92540b; font-size: 16px; }
.overview-grid { display: grid; grid-template-columns: minmax(0, 1.75fr) minmax(310px, .9fr); align-items: stretch; gap: 14px; }
.panel { min-width: 0; padding: 16px; border-radius: 14px; background-color: var(--aq-overview-panel-fill); background-image: none; box-shadow: inset 0 1px #fff, 0 3px 9px -6px #23486365; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.section-head h2 { margin: 0; color: #173850; font-size: 18px; line-height: 1.35; font-weight: 690; }
.section-head p { margin-top: 4px; font-size: 12px; }
.map-status { flex: none; border-radius: 7px; background: #fff3df; color: #74430b; font-size: 11px; }
.map-unavailable { display: grid; grid-template-columns: 78px minmax(0, 1fr); align-items: center; gap: 18px; min-height: 134px; margin: 14px 0; padding: 18px 20px; border-radius: 10px; background-color: var(--aq-content-strong); background-image: var(--aq-overview-map-image); }
.map-unavailable strong { display: block; color: #173850; font-size: 15px; }
.map-unavailable p { max-width: 57ch; margin: 6px 0 0; color: #405f75; font-size: 13px; line-height: 1.55; }
.map-icon { width: 52px; height: 52px; padding: 12px; border: 1px solid #8cb9d3; border-radius: 50%; background: #e8f6fd; color: #1473ac; }
.distribution-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid #d3e4ef; padding-top: 13px; }
.distribution-head h3 { margin: 0; font-size: 15px; color: #173850; }
.compact-field { display: flex; align-items: center; gap: 7px; font-size: 12px; color: #405f75; }
.compact-field select, .field input, .field select { min-height: 36px; border: 1px solid #afc9da; border-radius: 7px; padding: 6px 9px; color: #173850; background: #f8fcff; font: inherit; }
.grade-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px 10px; padding-top: 10px; }
.grade-row { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto auto; align-items: center; gap: 6px; min-width: 0; padding: 7px; border: 1px solid transparent; border-radius: 7px; color: #21465e; background: transparent; text-align: left; cursor: pointer; }
.grade-row[aria-pressed="true"] { border-color: #2d80bf; background: #e6f4fd; }
.grade-row strong { color: #173850; font-variant-numeric: tabular-nums; }.grade-row small { color: #48667a; }
.grade-swatch { width: 10px; height: 10px; border-radius: 50%; background: #58bd77; }.grade-swatch[data-grade="2"] { background: #e2ca26; }.grade-swatch[data-grade="3"] { background: #ec903c; }.grade-swatch[data-grade="4"] { background: #d95449; }.grade-swatch[data-grade="5"] { background: #b74e7a; }.grade-swatch[data-grade="6"] { background: #83506f; }
.ranking-panel { display: flex; flex-direction: column; }
.top-five { padding: 4px 8px; font-size: 11px; font-weight: 650; }
.segmented { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; margin-top: 15px; padding: 3px; border-radius: 9px; background: #e6f0f7; }
.segmented button { min-height: 34px; border: 0; border-radius: 6px; color: #405f75; background: transparent; font: inherit; cursor: pointer; }
.segmented button[aria-pressed="true"] { color: white; background: #1673b9; box-shadow: 0 2px 5px #174f7933; }
.ranking-list { display: grid; gap: 0; margin: 9px 0 6px; padding: 0; list-style: none; }
.ranking-list li + li { border-top: 1px solid #d9e7ef; }
.ranking-row { display: grid; grid-template-columns: 30px minmax(0, 1fr) auto; align-items: center; width: 100%; gap: 8px; min-height: 57px; padding: 6px 3px; border: 0; color: inherit; background: transparent; text-align: left; cursor: pointer; }
.ranking-row[aria-current="true"] { background: #eaf5fc; }
.rank-index { color: #42677e; font-size: 12px; font-variant-numeric: tabular-nums; }.rank-name { min-width: 0; color: #173850; font-weight: 650; }.rank-name small { display: block; margin-top: 2px; color: #48667a; font-size: 11px; font-weight: 400; }.aqi-value { align-self: center; color: #173850; font-size: 19px; font-variant-numeric: tabular-nums; }.grade-chip { grid-column: 2 / 4; justify-self: start; padding: 2px 7px; border-radius: 4px; background: #e7f5ec; color: #286740; font-size: 10px; }
.ranking-row .grade-chip { white-space: nowrap; }
.grade-chip[data-grade="3"] { background: #fff0de; color: #834705; }.grade-chip[data-grade="4"], .grade-chip[data-grade="5"], .grade-chip[data-grade="6"] { background: #fde8e5; color: #822c2a; }
.text-action { display: inline-flex; align-items: center; gap: 5px; padding: 5px 2px; border: 0; color: #075aa4; background: transparent; font: inherit; font-size: 13px; font-weight: 620; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }.text-action:disabled { opacity: .5; cursor: not-allowed; }
.quiet-note, .loading-line, .empty-cell { color: #405f75; font-size: 13px; line-height: 1.55; }.quiet-note { margin: 12px 0; }.loading-line { padding: 26px 8px; }.error-line { color: #8a2b2b; font-size: 13px; }
.city-table-panel { padding-bottom: 10px; }.table-title-row { align-items: center; }.filter-bar { display: grid; grid-template-columns: 1.3fr 1fr minmax(120px, .7fr) minmax(150px, .8fr); align-items: end; gap: 10px; margin: 15px 0 12px; }.field { display: grid; gap: 5px; min-width: 0; color: #405f75; font-size: 11px; }.field input, .field select { width: 100%; min-width: 0; font-size: 13px; }.search-control { display: flex; align-items: center; gap: 6px; padding: 0 8px; border: 1px solid #afc9da; border-radius: 7px; background: #f8fcff; color: #42677e; }.search-control input { border: 0; outline: 0; padding-inline: 0; }.search-control button { border: 0; background: transparent; color: #42677e; cursor: pointer; font-size: 18px; }.table-scroll { width: 100%; overflow: auto; border-radius: 7px; outline-offset: 2px; }.observation-table { width: 100%; min-width: 720px; border-collapse: collapse; font-size: 13px; }.observation-table th, .observation-table td { padding: 10px 12px; border-bottom: 1px solid #d5e5ef; text-align: left; white-space: nowrap; }.observation-table thead { background: #dceaf5; color: #31556d; font-size: 11px; }.observation-table tbody tr:hover, .observation-table tbody tr.selected { background: #edf7fc; }.observation-table th[scope="row"] { font-weight: 600; }.table-city-link { padding: 0; border: 0; color: #075aa4; background: transparent; font: inherit; font-weight: 650; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }.numeric { font-variant-numeric: tabular-nums; text-align: right !important; }.table-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding-top: 10px; color: #48667a; font-size: 12px; }.empty-cell { padding: 28px !important; text-align: center !important; }
.btn { display: inline-flex; justify-content: center; align-items: center; gap: 6px; min-height: 36px; padding: 6px 11px; border: 1px solid #a9c5d8; border-radius: 7px; color: #21465e; background: #f6fbfe; font: inherit; font-size: 12px; cursor: pointer; }.btn:disabled { opacity: .5; cursor: not-allowed; }
.summary-strip > div { grid-template-columns: minmax(0, 1fr); align-content: start; }
.summary-strip strong { grid-row: auto; line-height: 1.25; overflow-wrap: anywhere; }
.section-head > div, .heading-meta { min-width: 0; }
.grade-row { min-height: 40px; grid-template-columns: 12px minmax(0, 1fr) auto; }
.grade-row small { display: none; }
.grade-chip[data-grade="0"] { background: var(--aq-content-strong); color: var(--aq-secondary); }
.grade-chip[data-grade="1"] { background: #d7eddf; color: #196042; }
.grade-chip[data-grade="2"] { background: #f3eaca; color: #6b521e; }
.grade-chip[data-grade="3"] { background: #fae2c8; color: #81461e; }
.grade-chip[data-grade="4"] { background: #f6dcdc; color: #943b40; }
.grade-chip[data-grade="5"] { background: #edd9e5; color: #79264f; }
.grade-chip[data-grade="6"] { background: #e9d3da; color: #6a293c; }
.grade-swatch { border: 1px solid var(--aq-secondary); background: #00e400; }
.grade-swatch[data-grade="2"] { background: #ffff00; }
.grade-swatch[data-grade="3"] { background: #ff7e00; }
.grade-swatch[data-grade="4"] { background: #ff0000; }
.grade-swatch[data-grade="5"] { background: #99004c; }
.grade-swatch[data-grade="6"] { background: #7e0023; }
.grade-legend { margin: 10px 0; color: var(--aq-secondary); font-size: 13px; }
.grade-legend summary { width: fit-content; min-height: 32px; padding: 5px 0; color: var(--aq-link); cursor: pointer; }
.grade-legend dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 116px), 1fr)); gap: 6px 12px; margin: 5px 0 8px; }
.grade-legend dd, .selected-readings dd { margin: 0; }
.grade-legend dt { font-weight: 620; }
.grade-legend p { margin: 0; }
.selection-notice { margin: 0; }
.selected-panel .section-head { align-items: center; }
.selected-panel .section-head h2 { margin-top: 5px; }
.selected-panel .section-head .btn { flex: none; }
.selected-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr); gap: 20px; margin-top: 14px; }
.selected-l { min-width: 0; }
.selected-readings { display: grid; gap: 8px; margin: 0; }
.selected-readings > div { padding: 8px 0; border-bottom: 1px solid var(--aq-border); }
.selected-readings dt { color: var(--aq-text); }
.selected-readings dd { color: var(--aq-secondary); font-size: 13px; overflow-wrap: anywhere; }
.selected-readings strong { margin-inline-end: 6px; font-size: 18px; font-variant-numeric: tabular-nums; }
.filter-bar { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) auto minmax(0, .85fr) minmax(0, .9fr); }
.page-controls { display: flex; flex-wrap: wrap; gap: 8px; }
.table-series { display: block; min-height: 25px; line-height: 25px; }
.table-city-link, .text-action { min-height: 32px; }
.table-city-link { text-align: left; }
.table-scroll { max-width: 100%; }
.observation-table th:first-child { position: sticky; z-index: 1; left: 0; width: 1%; max-width: 12em; background: var(--aq-content); white-space: normal; overflow-wrap: anywhere; }
.observation-table thead th:first-child { background: #dceaf5; }
.observation-table tr.selected > th:first-child { background: #eaf5fc; box-shadow: inset 3px 0 var(--aq-focus); }
.overview-page h2[tabindex="-1"] { scroll-margin-top: 84px; }
.search-control:focus-within { outline: 2px solid var(--aq-focus); outline-offset: 2px; }
.applied-filters { overflow-wrap: anywhere; }
:focus-visible { outline: 3px solid #1475b9; outline-offset: 2px; }
@media (max-width: 1000px) { .overview-grid { grid-template-columns: minmax(0, 1.25fr) minmax(280px, .9fr); }.filter-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .overview-page { gap: 12px; }.overview-heading { align-items: flex-start; flex-direction: column; }.heading-meta { justify-content: flex-start; }.page-heading h1 { font-size: 24px; }.summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }.summary-strip > div { padding: 10px 12px; }.overview-grid { grid-template-columns: minmax(0, 1fr); }.map-unavailable { grid-template-columns: 56px minmax(0, 1fr); padding: 14px; gap: 12px; }.map-icon { width: 48px; height: 48px; padding: 10px; }.grade-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }.filter-bar { grid-template-columns: minmax(0, 1fr); }.table-footer { align-items: flex-start; flex-direction: column; }.panel { padding: 13px; } }
.filter-apply { align-self: end; }
@media (max-width: 700px) {
  .selected-layout { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .grade-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .map-unavailable { min-height: 0; }
  .grade-legend summary, .text-action, .btn, .table-city-link, .compact-field select, .field input, .field select { min-height: 44px; }
  .grade-legend summary { display: list-item; align-content: center; }
  .rank-name { overflow-wrap: anywhere; }
  .map-status { white-space: nowrap; }
  .observation-table th:first-child { width: 1%; min-width: 95px; max-width: 116px; }
}
@media (forced-colors: active) { .panel, .summary-strip > div { border: 1px solid CanvasText; background: Canvas; color: CanvasText; box-shadow: none; }.grade-chip, .status, .snapshot-time { border: 1px solid currentColor; } }
</style>
