<script setup lang="ts">
import { computed } from 'vue'
import { Grid, Finished, Warning, Tickets } from '@element-plus/icons-vue'
import ChartView from '../shared/ChartView.vue'
import { coverageChart, lineChart } from './charts'
import { coverage, cities, expected, missing, pollutants, hourly } from './snapshot'
const props = defineProps<{ hours: number }>()
const emit = defineEmits<{ window: [hours: number]; ledger: [] }>()
const primaryCount = pollutants.reduce((sum, p) => sum + p.count, 0)
const primaryRows = pollutants.map(p => ({ ...p, percent: `${(100 * p.count / primaryCount).toFixed(1)}%` }))
const series = computed(() => lineChart(props.hours))
</script>
<template>
  <div class="sky-stats">
    <section class="sky-glass stat-panel pollutant-panel"><div class="stat-head"><h2>首要污染物分布</h2><small>按城市数</small></div>
      <ul class="primary-bars"><li v-for="p in primaryRows" :key="p.name">
        <span>{{ p.name }}</span><i class="bar-track" aria-hidden="true"><i :style="{ width: p.percent }"/></i><b>{{ p.count }} 城</b><span>{{ p.percent }}</span>
      </li></ul><div class="stat-foot">占有首要污染物的 {{ primaryCount }} 个样本城市</div>
    </section>
    <section class="sky-glass stat-panel trend-panel"><div class="stat-head"><h2>样本到报趋势</h2><div class="segmented sub-segment" role="group" aria-label="到报趋势时间范围"><button v-for="n in [6,12]" :key="n" :aria-pressed="hours === n" @click="emit('window', n)">{{ n }} 小时</button></div></div>
      <div class="trend-key"><i/>到报城市数（城）<button class="sky-text-link" aria-label="查看趋势数据表" @click="emit('ledger')"><Tickets aria-hidden="true"/>数据表</button></div>
      <ChartView :option="series" :label="`最近 ${hours} 小时合成到报城市数，${hourly.at(-1)?.time} 为 ${cities.length} 城`" height="100%"/>
    </section>
    <section class="sky-glass stat-panel coverage-panel"><div class="stat-head"><h2>样本覆盖情况</h2><small>同刻名录</small></div><div class="cover-body">
      <div class="ring-holder coverage-ring"><ChartView :option="coverageChart" :label="`样本到报率 ${coverage}%`" height="100%"/><div class="ring-center"><b>{{ coverage }}<small>%</small></b><span>到报率</span></div></div>
      <dl class="cover-list"><div><dt><Finished aria-hidden="true"/>已到报</dt><dd>{{ cities.length }}</dd></div><div><dt><Warning aria-hidden="true"/>尚缺报</dt><dd>{{ missing.length }}</dd></div><div><dt><Grid aria-hidden="true"/>名录总数</dt><dd>{{ expected }}</dd></div></dl>
    </div></section>
  </div>
</template>
