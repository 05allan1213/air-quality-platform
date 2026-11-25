<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

type Point = { observed_at: string; state: string; results: { indicator: string; value: string | null; quality: string; unit: string }[] }
const props = defineProps<{ points: Point[]; label: string }>()
const host = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

function render() {
  if (!host.value || !chart) return
  const values = props.points.map(point => point.results.find(row => row.indicator === 'AQI' && row.quality === 'valid')?.value ?? null)
  const labels = props.points.map(point => new Date(point.observed_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
  chart.setOption({
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    animationDuration: 220,
    grid: { top: 18, right: 16, bottom: 34, left: 46 },
    tooltip: { trigger: 'axis', valueFormatter: (value: string | number | null) => value == null ? '无有效值' : String(value) },
    xAxis: { type: 'category', data: labels, boundaryGap: false, axisTick: { show: false }, axisLine: { lineStyle: { color: '#9db9cb' } }, axisLabel: { color: '#405f75', hideOverlap: true } },
    yAxis: { type: 'value', name: 'AQI', nameTextStyle: { color: '#405f75' }, axisLabel: { color: '#405f75' }, splitLine: { lineStyle: { color: '#d6e5ef' } } },
    series: [{ id: 'aqi', name: 'T 城市趋势', type: 'line', data: values, connectNulls: false, smooth: false, symbol: 'circle', symbolSize: 6, lineStyle: { color: '#087dbb', width: 2 }, itemStyle: { color: '#087dbb', borderColor: '#f6fbff', borderWidth: 2 }, areaStyle: { color: 'rgba(91, 176, 219, .12)' } }],
  }, { notMerge: true })
  chart.resize()
}

onMounted(() => {
  if (!host.value) return
  chart = echarts.init(host.value, undefined, { renderer: 'canvas' })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
  render()
})
watch(() => props.points, render, { deep: true })
onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="trend-chart-wrap">
    <div ref="host" class="trend-chart" role="img" :aria-label="label"></div>
    <details class="trend-data"><summary>查看 24 小时数据表</summary><div class="table-scroll" tabindex="0"><table><thead><tr><th scope="col">时刻</th><th scope="col">AQI</th><th scope="col">状态</th></tr></thead><tbody><tr v-for="point in points" :key="point.observed_at"><th scope="row">{{ new Date(point.observed_at).toLocaleString('zh-CN') }}</th><td>{{ point.results.find(row => row.indicator === 'AQI')?.value ?? '无有效值' }}</td><td>{{ point.state === 'ok' ? '已返回' : '该时槽无数据' }}</td></tr></tbody></table></div></details>
  </div>
</template>

<style scoped>
.trend-chart-wrap { min-width: 0; }
.trend-chart { width: 100%; height: 250px; min-height: 220px; }
.trend-data { color: #21465e; font-size: 12px; }
.trend-data summary { width: fit-content; padding: 6px 0; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.trend-data .table-scroll { max-height: 220px; overflow: auto; }
.trend-data table { width: 100%; border-collapse: collapse; font-size: 12px; }
.trend-data th, .trend-data td { padding: 7px 9px; border-bottom: 1px solid #d5e5ef; text-align: left; }
@media (max-width: 600px) { .trend-chart { height: 210px; } }
@media (prefers-reduced-motion: reduce) { .trend-chart-wrap :deep(*) { scroll-behavior: auto; } }
</style>
