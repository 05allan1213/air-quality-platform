<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElDialog } from 'element-plus'
import { ArrowLeft, ArrowRight, Grid, List } from '@element-plus/icons-vue'
import { usePreviewStore } from '../shared/preview'
import { directory, displayValue, formula, indicators, loadHistory, mockStation, sourceTime, type HistoryPanel, type Indicator } from './fixture'

const store = usePreviewStore()
const tab = ref<'history' | 'stations'>('history')
const date = ref('2024-05-16')
const indicator = ref<Indicator>('AQI')
const history = ref<HistoryPanel>()
const condition = ref('')
const page = ref(0)
const loading = ref(false)
const failed = ref(false)
const station = ref<ReturnType<typeof mockStation>>()
let request = 0
const pageRows = computed(() => history.value?.results.slice(page.value * 6, (page.value + 1) * 6) ?? [])
async function query() {
  const current = ++request
  loading.value = true; failed.value = false
  const label = `${date.value} · ${formula[indicator.value]}`
  try {
    const data = await loadHistory(store.cityState, date.value, indicator.value)
    if (current !== request) return
    history.value = data; condition.value = label; page.value = 0
  } catch { if (current === request) { failed.value = true; history.value = undefined } }
  finally { if (current === request) loading.value = false }
}
watch(() => store.cityState, () => { request++; history.value = undefined; loading.value = false; failed.value = false })
function openStations() { tab.value = 'stations' }
defineExpose({ openStations })
</script>
<template>
  <section id="city-ledger" class="panel ledger" aria-label="城市历史与站点目录">
    <div class="ledger-nav"><button :aria-pressed="tab === 'history'" @click="tab = 'history'"><List aria-hidden="true" />已存城市历史</button><button :aria-pressed="tab === 'stations'" @click="tab = 'stations'"><Grid aria-hidden="true" />同城站点 <span>{{ directory.length }}</span></button></div>
    <div v-if="tab === 'history'" class="ledger-body"><p>查询 C 产品的指定地方日，按观测时刻读取已存修订。</p>
      <form class="ledger-form" @submit.prevent="query"><label>观测日期<select v-model="date" name="history-date"><option>2024-05-16</option><option>2024-05-17</option></select></label><label>指标<select v-model="indicator" name="history-indicator"><option v-for="i in indicators" :key="i" :value="i">{{ formula[i] }}</option></select></label><button class="btn" :disabled="loading">{{ loading ? '查询中…' : '查询历史' }}</button></form>
      <div role="status" aria-live="polite"><p v-if="failed" class="query-summary">当前 F 读取失败，请切换样本后重试。</p><p v-else-if="history" class="query-summary">{{ condition }} · {{ history.state === 'no_data' ? '指定窗口无记录' : `共 ${history.results.length} 条记录` }} · UTC+8</p></div>
      <template v-if="history && history.results.length"><div class="table-wrap" tabindex="0" aria-label="城市历史查询结果"><table><thead><tr><th>观测时刻</th><th>指标</th><th>数值</th><th>质量</th><th>修订</th></tr></thead><tbody><tr v-for="row in pageRows" :key="row.key"><td>{{ sourceTime(row.observed_at) }}</td><td>{{ formula[row.indicator] }}</td><td>{{ displayValue(row) }} {{ row.unit === '1' ? '' : row.unit }}</td><td>{{ row.quality === 'valid' ? '有效' : row.quality === 'censored' ? '低于检出限' : '缺测' }}</td><td>r{{ row.revision_no }}</td></tr></tbody></table></div><div class="pager"><button class="icon-btn" aria-label="历史上一页" :disabled="page === 0" @click="page--"><ArrowLeft aria-hidden="true" /></button><span>{{ page + 1 }} / {{ Math.ceil(history.results.length / 6) }}</span><button class="icon-btn" aria-label="历史下一页" :disabled="(page + 1) * 6 >= history.results.length" @click="page++"><ArrowRight aria-hidden="true" /></button></div></template>
    </div>
    <div v-else class="ledger-body"><p>S 产品 · 云汀市 · 依站址期区分对象</p><div class="station-row" v-for="s in directory" :key="s.site_epoch"><div><b>{{ s.station_name }}</b><small>{{ s.codes[0] }} · 站址期 {{ s.site_epoch }}</small></div><button class="link-btn" :aria-label="`查看${s.station_name}站址摘要`" @click="station = mockStation(s.site_epoch)">站址摘要<ArrowRight aria-hidden="true" /></button></div></div>
    <ElDialog :model-value="!!station" title="站址摘要" width="min(460px, calc(100% - 28px))" align-center @update:model-value="station = undefined"><template v-if="station"><h3>{{ station.station_name }}</h3><p class="dialog-intro">F 合成 · S 产品 · {{ station.city_name }}</p><dl class="site-dl"><dt>来源站码</dt><dd>{{ station.codes.join('、') }}</dd><dt>站址期</dt><dd>{{ station.site_epoch }}</dd><dt>有效起日</dt><dd>{{ sourceTime(station.period_start, 'YYYY-MM-DD') }}</dd><dt>坐标依据</dt><dd>未提供</dd></dl><p class="dialog-intro">完整站点详情页在本轮原型范围外。</p></template><template #footer><button class="btn primary" @click="station = undefined">关闭摘要</button></template></ElDialog>
  </section>
</template>
<style scoped>
.ledger-nav{display:flex;gap:20px;align-items:center;border-bottom:1px solid var(--line);padding:0 24px}.ledger-nav button{position:relative;display:flex;align-items:center;gap:7px;font-weight:580;color:var(--muted);min-height:48px;padding:0 2px}.ledger-nav button[aria-pressed=true]{color:var(--brand)}.ledger-nav button[aria-pressed=true]::after{content:'';height:2px;position:absolute;bottom:-1px;inset-inline:0;background:var(--brand)}.ledger-nav span{font-size:11px;color:var(--muted);background:#edf1f5;padding:0 5px;border-radius:4px}.ledger-body{padding:18px 24px}.ledger-body>p,.ledger-body .query-summary{color:var(--muted);font-size:13px}.ledger-form{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-top:12px}.ledger-form label{display:grid;gap:4px;color:var(--muted);font-size:12px}.ledger-form select{min-width:132px;font-size:14px}.query-summary{margin:15px 0 10px}.pager{display:flex;align-items:center;justify-content:end;gap:12px;margin-top:10px}.pager .icon-btn{background:white;border-radius:5px}.pager .icon-btn:disabled{opacity:.5}.station-row{display:flex;align-items:center;justify-content:space-between;gap:12px;border-top:1px solid var(--line-light);padding:12px 0}.station-row:first-of-type{margin-top:12px}.station-row small{display:block;color:var(--muted)}.site-dl{display:grid;grid-template-columns:100px 1fr;gap:8px}.site-dl dt{color:var(--muted)}.site-dl dd{margin:0}.ledger-body td,.ledger-body th{white-space:nowrap}@media(max-width:600px){.ledger-body,.ledger-nav{padding-inline:15px}.ledger-form select{min-width:0}.ledger-form{gap:10px}}
</style>
