<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElDialog } from 'element-plus'
import { ArrowRight, Clock, Connection, DataAnalysis, Document, Finished, InfoFilled, MapLocation, OfficeBuilding, Refresh, Star, StarFilled, Warning } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { usePreviewStore } from '../shared/preview'
import { ANCHOR, CITY_NAME, colors, displayValue, formula, grades, loadCity, pollutantNames, sourceTime, type CityPanel, type TrendPanel } from './fixture'
import CityTrend from './CityTrend.vue'
import CityLedger from './CityLedger.vue'

const route = useRoute()
const router = useRouter()
const store = usePreviewStore()
const city = ref<CityPanel>()
const trend = ref<TrendPanel>()
const loading = ref(true)
const failed = ref(false)
const detail = ref(false)
const ledger = ref<InstanceType<typeof CityLedger>>()
function showStations() {
  ledger.value?.openStations()
  const node = document.getElementById('city-ledger')
  node?.scrollIntoView({ block: 'start' })
  node?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')?.focus({ preventScroll: true })
}
let generation = 0
const aqi = computed(() => city.value?.slots.find(slot => slot.indicator === 'AQI')?.results[0])
const valid = computed(() => city.value?.slots.filter(s => s.results[0]?.quality === 'valid').length ?? 0)
const pollutants = computed(() => city.value?.slots.filter(slot => slot.indicator !== 'AQI') ?? [])
const panelState = computed(() => store.cityState === 'empty' ? '当前窗口无数据' : store.cityState === 'stale' ? '时效未知' : valid.value < 7 ? '部分缺测' : '数据完整')
function favorite() { store.favorite = !store.favorite; store.notify(store.favorite ? '已收藏云汀市 · F 演示' : '已取消收藏云汀市') }
async function read() {
  const version = ++generation
  loading.value = true; failed.value = false; detail.value = false
  try {
    const result = await loadCity(store.cityState)
    if (version === generation) { city.value = result.city; trend.value = result.trend }
  } catch { if (version === generation) { failed.value = true; city.value = undefined; trend.value = undefined } }
  finally { if (version === generation) loading.value = false }
}
function retry() { store.cityState = 'normal'; void router.replace({ query: { ...route.query, state: 'normal' } }) }
watch(() => store.cityState, read, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>
<template>
  <div class="page-title city-title"><div class="title-copy"><div class="city-name"><h1>{{ CITY_NAME }}</h1><span class="place-type"><MapLocation aria-hidden="true" />城市观测</span></div><p><Clock aria-hidden="true" /><time :datetime="ANCHOR">2024 年 05 月 16 日　23:00</time><span class="timezone">UTC+8</span></p></div>
    <div class="actions"><button class="btn" :aria-pressed="store.favorite" @click="favorite"><component :is="store.favorite ? StarFilled : Star" aria-hidden="true" />{{ store.favorite ? '已收藏' : '收藏城市' }}</button><RouterLink class="btn primary" to="/analysis?from=city"><DataAnalysis aria-hidden="true" />进入对比</RouterLink></div>
  </div>
  <p v-if="store.cityState !== 'normal'" class="compare-context">进入对比将使用研究区独立的标准 F 样本。</p>
  <div v-if="loading" class="panel city-placeholder" role="status" aria-live="polite"><Clock aria-hidden="true" /><h2>正在读取城市观测…</h2><p>本地合成快照 · C 和 T 产品</p></div>
  <div v-else-if="failed" class="panel city-placeholder" role="alert"><Warning aria-hidden="true" /><h2>城市观测读取失败</h2><p>本次模拟读取遇到错误，可以重新加载正常样本。</p><button class="btn primary" @click="retry"><Refresh aria-hidden="true" />重新加载</button></div>
  <template v-else-if="city && trend">
    <div v-if="store.cityState === 'stale' || store.cityState === 'partial' || store.cityState === 'empty'" class="state-notice" role="status"><InfoFilled aria-hidden="true" /><span>{{ store.cityState === 'stale' ? '距观测已超过 15 小时。来源状态依据已过期，发布时间与当前时效未知。' : store.cityState === 'partial' ? '本时刻含缺测和低于检出限的指标。T 趋势保留原始空槽。' : '该指定窗口没有可展示的观测。请在演示设置中切换样本。' }}</span></div>
    <section class="panel snapshot" aria-label="城市空气质量快照"><div class="aqi-block">
      <div class="aqi-heading"><span class="aqi-symbol" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M3 8h11a2.5 2.5 0 1 0-2.4-3.2M2 12h16a2.4 2.4 0 1 1-2.3 3M5 16h4.2a2.4 2.4 0 1 1-2.3 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></span><h2>城市空气质量</h2></div>
      <div class="aqi-value"><strong>{{ displayValue(aqi) }}</strong><div><span>AQI</span><b v-if="aqi?.grade" class="aqi-grade"><i :style="{background:aqi.color || undefined}" />{{ grades[aqi.grade - 1] }}<span> / 一级</span></b><b v-else class="muted">无读数</b></div></div>
      <div class="aqi-rail" role="img" :aria-label="aqi?.grade ? `标准六级色带，当前一级，优，AQI ${displayValue(aqi)}` : '标准六级色带，当前没有等级'"><span v-if="aqi?.grade" class="rail-marker" /><i v-for="(color,i) in colors" :key="color" :style="{'--grade-color': color}" :class="{ active: i === 0 && aqi?.grade === 1 }" /></div>
      <div class="rail-ends"><span>优</span><span>严重污染</span></div>
      <div class="standard"><span>标准依据</span><strong>HJ 633—2012</strong></div>
    </div>
      <div class="pollutant-block"><div class="pollutant-heading"><div><span class="product-code">C</span><h2>污染物浓度</h2></div><span class="status-inline" :class="{ caution:store.cityState !== 'normal' }"><i />{{ panelState }}</span></div>
      <div class="pollutants"><div v-for="slot in pollutants" :key="slot.indicator" class="pollutant"><div class="chem"><b>{{ formula[slot.indicator] }}</b><span>{{ pollutantNames[slot.indicator] }}</span></div><div class="measurement"><strong>{{ displayValue(slot.results[0]) }}</strong><span>{{ slot.indicator === 'CO' ? 'mg/m³' : 'µg/m³' }}</span></div><small v-if="slot.results[0]?.quality === 'censored'">低于检出限</small><small v-else-if="slot.results[0]?.value == null">缺测</small></div></div>
      </div>
      <div class="snapshot-foot"><span><OfficeBuilding aria-hidden="true" />城市直接观测 <span class="foot-sep">/</span> C 产品</span><button class="link-btn" @click="detail = true">查看观测明细<ArrowRight aria-hidden="true" /></button></div>
    </section>
    <div class="city-secondary"><CityTrend :trend="trend" />
      <aside class="panel provenance"><div class="panel-head"><div><h2>数据来源</h2></div><Connection aria-hidden="true" /></div>
        <div class="source-context"><span class="source-tile"><Document aria-hidden="true" /></span><div><strong>F 合成观测源</strong><small class="mono">{{ city.source }}</small></div></div>
        <dl><dt>观测时刻</dt><dd>{{ aqi ? sourceTime(aqi.observed_at) : '无观测' }}</dd><dt>获取时刻</dt><dd>{{ aqi?.fetched_at ? sourceTime(aqi.fetched_at) : '未知' }}</dd><dt>来源状态</dt><dd>{{ aqi?.source_status === 'responded' ? '已有响应记录' : '未知' }}</dd><dt>指标可用</dt><dd><b>{{ valid }}</b> / 7 项</dd></dl>
        <div class="source-note"><span><Finished aria-hidden="true" /> Asia/Shanghai</span><button class="link-btn" @click="showStations">同城站点<ArrowRight aria-hidden="true" /></button></div>
      </aside></div>
    <CityLedger ref="ledger" />
    <ElDialog v-model="detail" title="城市观测明细" width="min(740px, calc(100% - 28px))" align-center destroy-on-close>
      <p class="dialog-intro">F 合成 · C 产品 · {{ sourceTime(city.snapshot_at, 'YYYY-MM-DD HH:mm') }} UTC+8</p>
      <div class="table-wrap detail-table" tabindex="0" aria-label="城市七项指标与修订"><table><thead><tr><th>指标</th><th>值</th><th>质量</th><th>修订 ID</th></tr></thead><tbody><tr v-for="slot in city.slots" :key="slot.indicator"><td>{{ formula[slot.indicator] }}</td><td>{{ displayValue(slot.results[0]) }} {{ slot.indicator === 'AQI' ? '' : slot.results[0]?.unit }}</td><td>{{ slot.results[0]?.quality === 'valid' ? '有效' : slot.results[0]?.quality === 'censored' ? '低于检出限' : '缺测' }}</td><td class="mono">{{ slot.results[0]?.revision ?? '—' }}</td></tr></tbody></table></div>
      <p class="dialog-intro">AQI 标准系列 {{ city.standard_series }}。浓度按小时标签展示，采样窗口端点未标明。</p>
      <template #footer><button class="btn primary" @click="detail = false">关闭明细</button></template>
    </ElDialog>
  </template>
</template>
<style scoped>
.city-name,.city-title .title-copy p{display:flex;align-items:center}.city-name{gap:12px}.place-type{font-size:12px;color:var(--muted);display:inline-flex;align-items:center;gap:4px;border-left:1px solid #c6d0df;padding-left:12px}.place-type svg,.city-title p svg{width:15px;height:15px}.city-title .title-copy p{gap:6px;font-size:13px}.timezone{font-size:11px;color:var(--muted);margin-left:2px}.city-placeholder{min-height:282px;display:grid;justify-items:center;align-content:center;gap:9px}.city-placeholder>svg{width:28px;height:28px;color:var(--brand)}.city-placeholder p{color:var(--muted)}.state-notice{display:flex;align-items:start;gap:8px;background:#fff5e3;color:#74460e;border:1px solid #ead7ad;border-radius:7px;margin-bottom:13px;padding:9px 14px;font-size:13px}.state-notice svg{width:16px;height:16px;flex:none;margin-top:2px}
.snapshot{display:grid;grid-template-columns:minmax(250px,.85fr) 2.15fr;overflow:hidden}.aqi-block{padding:22px 27px 16px;background:#f1f6f9;border-right:1px solid var(--line)}.aqi-heading,.pollutant-heading,.pollutant-heading>div,.aqi-value,.snapshot-foot,.snapshot-foot>span,.source-context,.source-note{display:flex;align-items:center}.aqi-heading,.pollutant-heading>div{gap:9px}.aqi-heading h2,.pollutant-heading h2{font-size:17px}.aqi-symbol{color:#426882;line-height:1}.aqi-symbol svg{width:22px;height:22px}.aqi-value{gap:14px;margin:6px 0 17px;line-height:1}.aqi-value>strong{font-size:clamp(64px,5.6vw,78px);letter-spacing:-.065em;font-weight:600;color:#18354b}.aqi-value>div{display:grid;justify-items:start;gap:6px}.aqi-value>div>span{font-size:12px;font-weight:650;color:#455d70;letter-spacing:.045em}.aqi-grade{display:flex;align-items:center;gap:5px;color:#165441;font-size:14px;white-space:nowrap}.aqi-grade i,.status-inline i{width:9px;height:9px;border-radius:50%;border:1px solid #25382d33}.aqi-grade span{color:#385d50;font-size:12px}.aqi-rail{display:flex;gap:3px;position:relative;height:5px}.aqi-rail i{height:5px;background:var(--grade-color);flex:1;border-radius:1px}.rail-marker{position:absolute;left:8%;top:-8px;border-left:3px solid transparent;border-right:3px solid transparent;border-top:5px solid #1a3544}.rail-ends{margin-top:5px;display:flex;justify-content:space-between;color:#506172;font-size:11px}.standard{margin-top:13px;display:flex;justify-content:space-between;gap:10px;align-items:center;font-size:12px;color:var(--muted)}.standard strong{font-weight:560;color:#30485e}.pollutant-block{padding:23px 26px 15px}.pollutant-heading{justify-content:space-between;gap:8px;margin-bottom:12px}.product-code{display:inline-grid;place-items:center;width:21px;height:21px;border:1px solid #c4d0df;border-radius:4px;color:#47617d;background:#f3f6fa;font:650 12px InterPreview,sans-serif}.status-inline{font-size:12px;color:var(--good);display:inline-flex;align-items:center;gap:6px}.status-inline i{width:6px;height:6px;border:0;background:#2a8262}.status-inline.caution{color:var(--warn)}.status-inline.caution i{background:#ae772b}.pollutants{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));column-gap:25px}.pollutant{padding:9px 0 10px}.pollutant:nth-child(n+4){border-top:1px solid var(--line-light)}.chem{display:flex;align-items:center;gap:7px;color:#4f6072}.chem b{font-size:14px;line-height:1.2;font-weight:650;color:#35475b}.chem span{font-size:12px}.measurement{display:flex;align-items:baseline;gap:6px;margin-top:4px}.measurement strong{font-size:27px;font-weight:580;line-height:1.2;letter-spacing:-.045em}.measurement span,.pollutant small{font-size:12px;color:var(--muted)}.pollutant small{color:var(--warn)}.snapshot-foot{grid-column:1/-1;justify-content:space-between;gap:8px;padding:5px 24px;background:#fff;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.snapshot-foot>span{gap:8px}.snapshot-foot svg{height:15px;width:15px}.snapshot-foot .link-btn{font-size:12px}.foot-sep{color:#8293a4}.city-secondary{display:grid;grid-template-columns:minmax(0,2.15fr) minmax(245px,.85fr);gap:18px;margin:19px 0}.provenance{display:flex;flex-direction:column}.provenance .panel-head>svg{color:#60778e;width:23px;height:23px}.source-context{gap:10px;margin:0 24px;padding:9px 10px;background:#f5f8fb;border:1px solid #e4eaf1;border-radius:6px}.source-tile{color:#526981}.source-tile svg{width:24px;height:24px}.source-context strong,.source-context small{display:block}.source-context strong{font-size:13px;font-weight:585}.source-context small{color:var(--muted);font-size:11px}.provenance dl{display:grid;grid-template-columns:1fr auto;gap:6px 10px;margin:12px 24px;color:var(--muted);font-size:13px}.provenance dd{margin:0;text-align:right;color:#304257}.source-note{padding:9px 0 13px;margin:auto 24px 0;border-top:1px solid var(--line-light);gap:6px;color:var(--muted);font-size:12px}.compare-context{font-size:12px;color:var(--muted);margin:-8px 0 12px}.source-note{justify-content:space-between;gap:6px}.source-note .link-btn{font-size:12px}.source-note svg{width:15px;height:15px}.detail-table{margin:15px 0}.detail-table th,.detail-table td{white-space:nowrap}
@media(max-width:940px){.snapshot{grid-template-columns:minmax(220px,.83fr) 1.8fr}.pollutant-block,.aqi-block{padding:19px}.pollutants{column-gap:16px}.chem{gap:4px;flex-wrap:wrap}.city-secondary{grid-template-columns:minmax(0,1.6fr) minmax(210px,.8fr);gap:12px}.provenance dl,.source-context,.source-note{margin-inline:15px}}
@media(max-width:700px){.snapshot{grid-template-columns:1fr}.aqi-block{border-right:0;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;gap:4px 18px;padding:17px 18px}.aqi-heading{grid-column:1/-1}.aqi-value{grid-column:1;grid-row:2/5;margin:0}.aqi-value>strong{font-size:61px}.aqi-value{gap:9px}.aqi-rail{grid-column:2;align-self:end;margin-top:14px}.rail-ends{grid-column:2;margin-top:0}.standard{grid-column:2;justify-content:space-between;flex-wrap:wrap;gap:2px;margin:0;font-size:10px}.city-secondary{grid-template-columns:1fr}.provenance dl{grid-template-columns:1fr 1fr}.city-title{flex-direction:row;align-items:center}.pollutant-block{padding:15px 17px 9px}.pollutant-heading{margin-bottom:6px}.pollutant-heading h2,.aqi-heading h2{font-size:16px}.pollutants{column-gap:9px}.chem{display:block}.chem span{display:block;font-size:11px}.measurement{gap:3px}.measurement strong{font-size:22px}.measurement span{font-size:10px}.snapshot-foot{padding:5px 15px}.provenance .panel-head{padding:16px}.source-note{margin-top:0}}
@media(max-width:520px){.city-title{flex-direction:column;align-items:flex-start;gap:10px}.city-title .actions{width:100%}.city-title .btn{min-height:35px}.place-type{font-size:11px}.city-secondary{margin:14px 0}.aqi-value>strong{font-size:58px}.aqi-value>div>span{font-size:11px}}
</style>
