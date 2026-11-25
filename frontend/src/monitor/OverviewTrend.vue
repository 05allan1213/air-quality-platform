<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { api, ApiError, usePrivateRead } from '../api/client'
import { standardLabel, utcLabel, type HomeReading } from './HomeProjection'
import type { ApiTarget } from './reads'

const TrendChart = defineAsyncComponent(() => import('./TrendChart.vue'))
const props = defineProps<{ target: ApiTarget | null; city: number; label: string; source: string; synthetic: boolean }>()
type Trend = {
  city: number; product: number; source: string; synthetic: boolean; anchor: string | null
  points: { observed_at: string; state: string; results: HomeReading[] }[]
}
const read = usePrivateRead<Trend>()
const series = ref('')
const seriesKey = (row: HomeReading) => JSON.stringify([row.standard_series, row.window_definition, row.unit])
const choices = computed(() => new Map((read.data.value?.points ?? []).flatMap(point => point.results)
  .filter(row => row.indicator === 'AQI')
  .map(row => [seriesKey(row), `${standardLabel(row.standard_series)} · ${row.window_definition} · ${row.unit}`])))
const activeSeries = computed(() => series.value || choices.value.keys().next().value)
const clockZone = Intl.DateTimeFormat().resolvedOptions().timeZone
const points = computed(() => {
  const key = activeSeries.value
  return (read.data.value?.points ?? []).map(point => {
    const results = point.results.filter(row => row.indicator === 'AQI' && seriesKey(row) === key && row.quality === 'valid')
    return { ...point, results, state: results.length ? 'ok' : 'no_data' }
  })
})
const hasValues = computed(() => points.value.some(point => point.results.some(row => row.value !== null)))

function load() {
  series.value = ''
  const { target, city, source, synthetic } = props
  if (!target) { read.clear(); return }
  void read.run(async signal => {
    const data = await api.get<Trend>(target.path, target.params, signal)
    if (data.city !== city || String(data.product) !== target.params.product || data.source !== source
      || data.synthetic !== synthetic || !data.anchor || Date.parse(data.anchor) !== Date.parse(target.params.anchor)) {
      throw new ApiError('趋势与选中城市不匹配。', 0, 'invalid_response')
    }
    return data
  })
}
watch(() => [props.city, props.source, props.synthetic, props.target?.path, JSON.stringify(props.target?.params)], load, { immediate: true })
</script>

<template>
  <div class="overview-trend" :aria-busy="read.busy.value">
    <h3>独立 T 城市趋势</h3>
    <p v-if="!target">当前选中城市没有可用的获准 T 趋势。</p>
    <p v-else-if="read.busy.value" role="status">正在读取独立 T 趋势…</p>
    <p v-else-if="read.error.value" role="status">
      当前选中城市的 T 趋势不可用。
      <button v-if="read.error.value.status !== 403" type="button" @click="load">重读趋势</button>
    </p>
    <template v-else-if="read.data.value">
      <p>T · {{ read.data.value.source }} · 产品 {{ read.data.value.product }} · 锚点 {{ utcLabel(read.data.value.anchor!) }}；不计入 L 表格或排名。</p>
      <label v-if="choices.size > 1">T 数据系列
        <select :value="activeSeries" @change="series = ($event.target as HTMLSelectElement).value">
          <option v-for="[key, name] in choices" :key="key" :value="key">{{ name }}</option>
        </select>
      </label>
      <p v-else-if="choices.size">{{ choices.values().next().value }}</p>
      <template v-if="hasValues">
        <p>图表时刻：{{ clockZone }}</p>
        <TrendChart :points="points" :label="`${label}独立 T 产品 24 小时 AQI 趋势，缺失时槽留空`" />
      </template>
      <p v-else>该 T 窗口没有可显示的有效 AQI。</p>
    </template>
  </div>
</template>

<style scoped>
.overview-trend { min-width: 0; }
.overview-trend h3 { margin: 0 0 4px; font-size: 15px; }
.overview-trend p, .overview-trend label { margin: 0 0 8px; color: var(--aq-secondary); font-size: 13px; overflow-wrap: anywhere; }
.overview-trend select { display: block; max-width: 100%; min-height: 36px; }
.overview-trend button { min-height: 32px; border: 0; color: var(--aq-link); background: transparent; text-decoration: underline; cursor: pointer; }
</style>
