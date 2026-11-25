<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import {
  chartSupport, columnLabel, metric, observationValue, qualityNames, stamp,
  type Result, type Support,
} from './analysisContract'

const props = defineProps<{ result: Result }>()
const emit = defineEmits<{ drill: [request: Support, label: string, event: MouseEvent] }>()
const names = { trend: '时间趋势', distribution: '样本分布', box: '箱线统计', heatmap: '地方时热图' }
type View = keyof typeof names
const selected = ref<View>('trend')
const showData = ref(false)
const host = ref<HTMLDivElement | null>(null)
const zone = computed(() => props.result.source_timezone)
const boxes = computed(() => (props.result.buckets ?? []).flatMap(bucket => bucket.box?.n && [bucket.box.q1, bucket.box.median, bucket.box.q3, bucket.box.lower_whisker, bucket.box.upper_whisker].every(value => value != null)
  ? [{ ...bucket, box: bucket.box }] : []))
const views = computed(() => (Object.keys(names) as View[]).filter(key => ({
  trend: Boolean(props.result.chart?.points.length), distribution: Boolean(props.result.distribution?.length),
  box: Boolean(boxes.value.length), heatmap: Boolean(props.result.heatmap?.length),
})[key]))
const active = computed(() => views.value.includes(selected.value) ? selected.value : views.value[0])
const activeName = computed(() => active.value ? names[active.value] : '')
const unit = computed(() => props.result.columns[0]?.unit ?? '')
const label = (pos: number) => columnLabel(props.result, pos)
const hourLabel = (hour: number) => `${String(hour).padStart(2, '0')}:00`
const trendSupport = (at: string, pos: number) => chartSupport(props.result, at, pos)
const rangeSupport = (at: string, pos: number, width: number) => chartSupport(props.result, at, pos, width)
const binLabel = ({ lower, upper }: { lower: number | null; upper: number | null }) => lower == null ? `< ${metric(upper)}` : upper == null ? `≥ ${metric(lower)}` : `[${metric(lower)}, ${metric(upper)})`
const colors = ['#0865b8', '#087562', '#9a560d', '#8e486e', '#435d89']
const axisColor = '#405f75'
const axisLine = { lineStyle: { color: '#acc6d8' } }
const xAxis = (data: string[], format?: (value: string) => string): echarts.XAXisComponentOption => ({
  type: 'category', data, axisLine, axisTick: { show: false },
  axisLabel: { color: axisColor, hideOverlap: true, formatter: format },
})
const yAxis = (name: string): echarts.YAXisComponentOption => ({
  type: 'value', name, nameTextStyle: { color: axisColor }, axisLabel: { color: axisColor },
  splitLine: { lineStyle: { color: '#d6e5ef' } },
})

function trend(chart: NonNullable<Result['chart']>): echarts.EChartsOption {
  const columns = props.result.columns
  const units = [...new Set(columns.map(column => column.unit))]
  const times = [...new Set(chart.points.map(point => point.at))]
  const points = new Map(chart.points.map(point => [`${point.pos}:${point.at}`, point]))
  const format = new Intl.DateTimeFormat('zh-CN', { timeZone: zone.value, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  const timeLabel = (value: string) => format.format(new Date(value))
  const series: echarts.LineSeriesOption[] = columns.flatMap((column, i) => {
    const name = label(column.pos)
    const color = colors[i % colors.length]
    const line: echarts.LineSeriesOption = {
      type: 'line', yAxisIndex: units.indexOf(column.unit), showSymbol: times.length <= 48,
      symbolSize: 5, connectNulls: false, itemStyle: { color }, lineStyle: { color, width: 2 },
    }
    const columnPoints = times.map(at => points.get(`${column.pos}:${at}`))
    if (chart.resolution === 'exact') return [{
      ...line, name, data: columnPoints.map(point => point && 'quality' in point && point.quality === 'valid' ? point.value : null),
    }]
    return ([['minimum', '下界'], ['maximum', '上界']] as const).map(([bound, title]) => ({
      ...line, name: `${name} ${title}`, lineStyle: { ...line.lineStyle, type: bound === 'minimum' ? 'dashed' : 'solid' },
      data: columnPoints.map(point => point && 'minimum' in point ? point[bound] : null),
    }))
  })
  return {
    grid: { top: 42, bottom: 46, left: 55, right: units.length > 1 ? 55 : 18 },
    legend: { type: 'scroll', bottom: 1, left: 10, right: 10, textStyle: { color: axisColor } },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: xAxis(times.map(timeLabel)), yAxis: units.map(yAxis), series,
  }
}

function option(): echarts.EChartsOption {
  const result = props.result
  const base: echarts.EChartsOption = {
    animation: false, color: colors,
    tooltip: { trigger: 'item', renderMode: 'richText' },
    grid: { top: 38, bottom: 38, left: 52, right: 18 },
  }
  if (active.value === 'trend' && result.chart) return { ...base, ...trend(result.chart) }
  if (active.value === 'distribution') {
    const bins = result.distribution ?? []
    return { ...base, xAxis: xAxis(bins.map(binLabel)), yAxis: yAxis('样本数'),
      series: [{ type: 'bar', name: '样本数', data: bins.map(bin => bin.n), barMaxWidth: 68 }] }
  }
  if (active.value === 'box') {
    const rows = boxes.value
    return { ...base,
      xAxis: xAxis(rows.map((_, i) => String(i + 1))), yAxis: yAxis(unit.value),
      series: [{ type: 'boxplot', name: `分桶箱线 · ${unit.value}`, data: rows.map(({ box }) =>
        [box.lower_whisker, box.q1, box.median, box.q3, box.upper_whisker].map(value => value ?? '-')
      ) }],
    }
  }
  const cells = result.heatmap ?? []
  const months = [...new Set(cells.map(cell => cell.month))]
  const data = cells.flatMap(cell => cell.mean == null ? [] : [[cell.hour, months.indexOf(cell.month), cell.mean]])
  const values = cells.flatMap(cell => cell.mean == null ? [] : [cell.mean])
  const min = values.length ? Math.min(...values) : 0
  const max = values.length ? Math.max(...values) : 0
  return { ...base,
    grid: { top: 16, left: 66, right: 12, bottom: 62 },
    xAxis: xAxis(Array.from({ length: 24 }, (_, hour) => hourLabel(hour))),
    yAxis: { type: 'category', inverse: true, data: months.map(month => month.slice(0, 7)), axisLabel: { color: axisColor }, axisLine },
    visualMap: { min, max, orient: 'horizontal', left: 'center', bottom: 4, text: [`高 · ${unit.value}`, '低'], textStyle: { color: axisColor }, inRange: { color: ['#dceffa', '#6eb4df', '#075aa4'] } },
    series: [{ type: 'heatmap', name: `均值 · ${unit.value}`, data }],
  }
}

let chart: echarts.ECharts | undefined
let observer: ResizeObserver | undefined
function render() {
  if (active.value) chart?.setOption(option(), { notMerge: true })
}
onMounted(() => {
  if (!host.value) return
  chart = echarts.init(host.value)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
  render()
})
watch(() => [active.value, props.result], render, { flush: 'post' })
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
function switchView(view: View) {
  if (active.value === view) return
  selected.value = view
  showData.value = false
}
</script>

<template>
  <section v-if="active" class="analysis-result-section" aria-labelledby="analysis-chart-title">
    <div class="analysis-section-heading">
      <h3 id="analysis-chart-title">分析图表</h3>
      <div v-if="views.length > 1" class="analysis-chart-modes" role="group" aria-label="图表视图">
        <button v-for="view in views" :key="view" type="button" :aria-pressed="view === active" @click="switchView(view)">{{ names[view] }}</button>
      </div>
    </div>
    <p v-if="active === 'trend' && result.chart?.resolution === 'range'">每 {{ metric(result.chart.interval_seconds / 3600) }} 小时的有效极值范围，不代表逐时值。有效数和缺口数见数据表。</p>
    <p v-else-if="active === 'trend'">源时区 {{ zone }}。未到报或无有效读数处不连线。</p>
    <p v-else-if="active === 'distribution'">有效样本按固定浓度区间（{{ unit }}）计数。</p>
    <p v-else-if="active === 'box'">横轴为表内分桶序号，单位 {{ unit }}。须端不等于全体极值。</p>
    <p v-else>按源地方时的月份与小时分组。空格不代表零值。</p>
    <div ref="host" class="analysis-chart-host" role="img" :aria-label="activeName" />

    <details :key="active" class="analysis-disclosure" @toggle="showData = ($event.target as HTMLDetailsElement).open">
      <summary>查看{{ activeName }}数据表</summary>
      <div v-if="showData" class="analysis-table-scroll" tabindex="0" role="region" :aria-label="`${activeName}数据表`">
        <table v-if="active === 'trend' && result.chart?.resolution === 'exact'" class="analysis-table">
          <thead><tr><th scope="col">观测时间</th><th scope="col">系列</th><th scope="col">读取值</th><th scope="col">质量</th><th scope="col">预期时槽</th><th scope="col">支撑记录</th></tr></thead>
          <tbody><tr v-for="point in result.chart.points" :key="`${point.pos}:${point.at}`"><th scope="row">{{ stamp(point.at, zone) }}</th><td>{{ label(point.pos) }}</td><td>{{ observationValue(point) }} {{ result.columns[point.pos]?.unit }}</td><td>{{ qualityNames[point.quality] ?? point.quality }}</td><td>{{ point.expected == null ? '未知' : point.expected ? '是' : '计划外' }}</td><td><button v-if="trendSupport(point.at, point.pos)" class="btn" type="button" @click="emit('drill', trendSupport(point.at, point.pos)!, `时序记录 · ${stamp(point.at, zone)}`, $event)">查看记录</button></td></tr></tbody>
        </table>
        <table v-else-if="active === 'trend' && result.chart?.resolution === 'range'" class="analysis-table">
          <thead><tr><th scope="col">区间起始</th><th scope="col">系列</th><th scope="col">下界 / 上界</th><th scope="col">有效数</th><th scope="col">缺口数</th><th scope="col">支撑记录</th></tr></thead>
          <tbody><tr v-for="point in result.chart.points" :key="`${point.pos}:${point.at}`"><th scope="row">{{ stamp(point.at, zone) }}</th><td>{{ label(point.pos) }}</td><td>{{ metric(point.minimum) }} / {{ metric(point.maximum) }} {{ result.columns[point.pos]?.unit }}</td><td>{{ point.valid_count }}</td><td>{{ point.gaps }}</td><td><button v-if="rangeSupport(point.at, point.pos, result.chart.interval_seconds)" class="btn" type="button" @click="emit('drill', rangeSupport(point.at, point.pos, result.chart.interval_seconds)!, `区间图样本 · ${stamp(point.at, zone)}`, $event)">查看记录</button></td></tr></tbody>
        </table>
        <table v-else-if="active === 'distribution'" class="analysis-table">
          <thead><tr><th scope="col">浓度区间（{{ unit }}）</th><th scope="col">有效样本数</th></tr></thead>
          <tbody><tr v-for="bin in result.distribution" :key="binLabel(bin)"><th scope="row">{{ binLabel(bin) }}</th><td>{{ bin.n }}</td></tr></tbody>
        </table>
        <table v-else-if="active === 'box'" class="analysis-table">
          <thead><tr><th scope="col">分桶</th><th scope="col">系列</th><th scope="col">有效数</th><th scope="col">下须 / Q1 / 中位数 / Q3 / 上须</th><th v-if="result.chart" scope="col">支撑记录</th></tr></thead>
          <tbody><tr v-for="(bucket, i) in boxes" :key="`${bucket.start}:${bucket.pos}`"><th scope="row">{{ i + 1 }} · {{ stamp(bucket.start, zone) }} 至 {{ stamp(bucket.end, zone) }}</th><td>{{ label(bucket.pos) }}</td><td>{{ bucket.box.n }}</td><td>{{ [bucket.box.lower_whisker, bucket.box.q1, bucket.box.median, bucket.box.q3, bucket.box.upper_whisker].map(metric).join(' / ') }} {{ unit }}</td><td v-if="result.chart"><button class="btn" type="button" @click="emit('drill', bucket.drilldown, `箱线分桶 ${i + 1} · ${label(bucket.pos)}`, $event)">有效样本</button></td></tr></tbody>
        </table>
        <table v-else class="analysis-table">
          <thead><tr><th scope="col">来源地方月份 / 小时</th><th scope="col">均值（{{ unit }}）</th><th scope="col">E / O / V</th><th v-if="result.chart" scope="col">支撑记录</th></tr></thead>
          <tbody><tr v-for="cell in result.heatmap" :key="`${cell.month}:${cell.hour}`"><th scope="row">{{ cell.month.slice(0, 7) }} · {{ hourLabel(cell.hour) }}</th><td>{{ metric(cell.mean) }}</td><td>{{ metric(cell.E) }} / {{ cell.O }} / {{ cell.V }}</td><td v-if="result.chart"><div class="analysis-buttons"><button class="btn" type="button" @click="emit('drill', cell.drilldown, `热图 · ${cell.month.slice(0, 7)} ${hourLabel(cell.hour)}`, $event)">有效样本</button><button class="btn" type="button" @click="emit('drill', cell.observed_drilldown, `热图 · ${cell.month.slice(0, 7)} ${hourLabel(cell.hour)}`, $event)">到报记录</button></div></td></tr></tbody>
        </table>
      </div>
    </details>
  </section>
</template>

<style scoped>
.analysis-chart-host { width: 100%; height: 282px; min-width: 0; background: var(--aq-content); border: 1px solid var(--aq-border); border-radius: 8px; }
.analysis-chart-modes { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.analysis-chart-modes button { min-height: 36px; padding: 6px 10px; border: 1px solid transparent; border-radius: 6px; color: var(--aq-secondary); background: transparent; cursor: pointer; }
.analysis-chart-modes button:hover { background: var(--aq-content); }
.analysis-chart-modes button[aria-pressed='true'] { border-color: var(--aq-control-border); background: var(--aq-content); color: var(--aq-link); font-weight: 620; }
@media (max-width: 600px) {
  .analysis-chart-host { height: 240px; }
  .analysis-chart-modes button { min-height: 44px; padding-inline: 6px; font-size: .85rem; }
}
</style>
