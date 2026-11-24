<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { ElDialog } from 'element-plus'
import { DataLine, List } from '@element-plus/icons-vue'
import ChartView from '../shared/ChartView.vue'
import { displayValue, sourceTime, type TrendPanel } from './fixture'

const props = defineProps<{ trend: TrendPanel }>()
const hours = ref(24)
const detail = ref(false)
const selected = ref<number | null>(null)
const visible = computed(() => props.trend.points.slice(-hours.value))
const counts = computed(() => visible.value.filter(point => point.results[0]?.value != null).length)
function openTable(index: number | null = null) { selected.value = index; detail.value = true }
const option = computed<EChartsOption>(() => ({
  animation: false, grid: { left: 38, right: 21, top: 20, bottom: 26 },
  xAxis: { type: 'category', boundaryGap: false, data: visible.value.map(p => sourceTime(p.observed_at, 'HH:mm')), axisLabel: { interval: hours.value === 24 ? 3 : 2, showMaxLabel: true, hideOverlap: true } },
  yAxis: { type: 'value', min: 0, max: 75, interval: 25, axisLine: { show: false } },
  tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, axisPointer: { type: 'line' }, valueFormatter: value => value == null ? '缺测' : String(value) },
  series: [{ type: 'line', name: 'T · AQI', data: visible.value.map(p => p.results[0]?.value == null ? null : Number(p.results[0].value)),
    smooth: false, connectNulls: false, showSymbol: true, symbol: 'circle', symbolSize: 5,
    itemStyle: { color: '#275ed5' }, lineStyle: { width: 2.7 }, areaStyle: { color: '#eaf1ff', opacity: .7 },
    emphasis: { scale: 1.5 },
  }],
}))
</script>
<template>
  <section class="panel trend-card" aria-labelledby="trend-title">
    <div class="panel-head"><div><h2 id="trend-title">近期 AQI 趋势</h2><p>T 产品独立序列 <span aria-hidden="true">·</span> 小时观测</p></div>
      <div class="switcher" aria-label="趋势显示范围"><button v-for="n in [24,12]" :key="n" :aria-pressed="hours === n" @click="hours = n">{{ n }} 小时</button></div>
    </div>
    <div class="plot-head"><span><i />城市 AQI</span><span>05月16日 · UTC+8</span></div>
    <ChartView v-if="counts" :option="option" :height="172" :label="`T 产品合成 AQI，${hours} 个小时中有 ${counts} 个有效读数。可通过趋势数据表读取。`" @point="openTable($event.dataIndex)" />
    <div v-else class="plot-empty"><DataLine aria-hidden="true" /><strong>所选窗口没有 T 观测</strong><span>保留 {{ hours }} 个应显示的小时位置。</span></div>
    <div class="plot-footer"><span><b>{{ counts }} / {{ hours }}</b> 有值时槽<span v-if="counts < hours"> · 缺口保留</span></span><button class="link-btn" @click="openTable()"><List aria-hidden="true" />趋势数据表</button></div>
    <ElDialog v-model="detail" title="趋势数据表" width="min(610px, calc(100% - 28px))" align-center destroy-on-close>
      <p class="dialog-intro">F 合成 · T 产品 · {{ hours }} 小时 · {{ trend.standard_series }}</p>
      <div class="table-wrap dialog-table" tabindex="0" aria-label="T 产品逐小时数据"><table><thead><tr><th>观测时刻（UTC+8）</th><th>AQI</th><th>时槽状态</th></tr></thead><tbody><tr v-for="point in selected == null ? visible : [visible[selected]!]" :key="point.observed_at"><td>{{ sourceTime(point.observed_at) }}</td><td>{{ displayValue(point.results[0]) }}</td><td>{{ point.state === 'ok' ? '有效读数' : '无数据' }}</td></tr></tbody></table></div>
      <template #footer><button v-if="selected != null" class="btn" @click="selected = null">显示全部时槽</button> <button class="btn primary" @click="detail = false">关闭数据表</button></template>
    </ElDialog>
  </section>
</template>
<style scoped>
.trend-card{display:flex;flex-direction:column}.switcher{display:flex;padding:3px;border-radius:7px;background:#edf1f6;gap:2px}.switcher button{color:var(--muted);border-radius:4px;padding:4px 10px;font-size:12px;min-height:28px;white-space:nowrap}.switcher button[aria-pressed=true]{background:white;color:var(--ink);box-shadow:0 1px 3px #23364d22;font-weight:650}.plot-head,.plot-footer{display:flex;align-items:center;justify-content:space-between;gap:8px;color:var(--muted);font-size:12px}.plot-head{padding:0 24px;margin-top:2px}.plot-head span:first-child{display:flex;align-items:center;gap:7px}.plot-head i{display:inline-block;height:3px;width:17px;border-radius:2px;background:var(--series-blue)}.chart-surface{margin-inline:8px;width:calc(100% - 16px)}.plot-footer{margin:4px 23px 0;padding:8px 0 12px;border-top:1px solid var(--line-light)}.plot-footer b{color:var(--ink)}.plot-empty{height:172px;display:grid;justify-items:center;align-content:center;gap:5px;color:var(--muted)}.plot-empty svg{width:24px;height:24px;color:var(--faint)}.dialog-table{margin-top:14px}.dialog-table th{position:sticky;top:0}.plot-footer .link-btn{font-size:12px}@media(max-width:600px){.trend-card .panel-head{align-items:flex-start}.switcher button{padding:3px 7px;min-height:32px}.plot-head,.plot-footer{font-size:11px}.plot-head{padding:0 15px}.plot-footer{margin-inline:15px}.chart-surface{width:100%;margin:0}}
</style>
