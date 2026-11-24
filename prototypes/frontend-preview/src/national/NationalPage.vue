<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NationalDialog, { type HomeDialog } from './NationalDialog.vue'
import { ArrowDown, ArrowRight, Bell, Calendar, Clock, Coin, DataLine, Fold, InfoFilled, MostlyCloudy, OfficeBuilding, UserFilled, Warning } from '@element-plus/icons-vue'
import ChartView from '../shared/ChartView.vue'
import NationalSide from './NationalSide.vue'
import SkyMetric from './SkyMetric.vue'
import AtlasMap from './AtlasMap.vue'
import NationalTable from './NationalTable.vue'
import NationalStats from './NationalStats.vue'
import { cities, coverage, distribution, expected, goodCount, lowFirst, metricLabels, missing, provinces, ranked, snapshot, type Metric } from './snapshot'
import { distributionChart } from './charts'
import { usePreviewStore } from '../shared/preview'
import './sky.css'

const emit = defineEmits<{ settings: []; workspace: []; scope: [name: string] }>()
const store = usePreviewStore()
const route = useRoute()
const router = useRouter()
const table = ref<InstanceType<typeof NationalTable>>()
const menu = ref(false)
const expanded = ref(false)
const dialog = ref<HomeDialog | null>(null)
const query = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : ''
const metric = computed(() => Object.hasOwn(metricLabels, query('metric')) ? query('metric') as Metric : 'AQI')
const low = computed(() => query('rank') === 'low')
const hours = computed(() => query('hours') === '6' ? 6 : 12)
const page = computed(() => Math.max(1, Math.floor(Number(query('page'))) || 1))
const metrics = [
  { label: '观测时刻', value: snapshot.hour, note: '2024 年 05 月 16 日', icon: Clock },
  { label: '数据产品', value: '全国城市列表', note: 'L 产品 · 小时观测', icon: Coin },
  { label: '同刻覆盖城市', value: cities.length, unit: '城', note: `应到名录 ${expected} 城`, icon: OfficeBuilding },
  { label: '优良城市', value: goodCount, unit: '城', note: `占可比样本 ${(goodCount / cities.length * 100).toFixed(1)}%`, icon: MostlyCloudy, fresh: true },
  { label: '样本到报率', value: coverage, unit: '%', note: `已到 ${cities.length} / 应到 ${expected}`, icon: DataLine },
]
const rankList = computed(() => (low.value ? lowFirst : ranked).slice(0, 5))
function setQuery(changes: Record<string, string | undefined>) { return router.replace({ query: { ...route.query, ...changes } }) }
async function list(changes: Record<string, string | undefined>) {
  await setQuery({ q: undefined, province: undefined, grade: undefined, order: undefined, page: undefined, ...changes })
  await nextTick(); table.value?.focus()
}
const validGrade = computed(() => /^[1-6]$/.test(query('grade')) ? query('grade') : '')
</script>
<template>
  <div class="sky-shell" :class="{ 'rail-expanded': expanded }">
    <NationalSide v-model:expanded="expanded" :mobile-open="menu" @close="menu = false" @workspace="emit('workspace')" @scope="emit('scope', $event)" @info="dialog = 'source'"/>
    <main id="content" class="sky-main" tabindex="-1">
      <header class="sky-top"><div class="top-title"><button class="sky-icon sky-hamburger" aria-label="打开导航" :aria-expanded="menu" @click="menu = true"><Fold aria-hidden="true"/></button><div><h1>全国空气质量总览</h1><p>同一观测时刻的城市空气质量、等级分布与样本覆盖。</p></div></div>
        <div class="top-actions"><span class="sky-pill date-pill"><Calendar aria-hidden="true"/><time :datetime="snapshot.observedAt">{{ snapshot.date }} {{ snapshot.hour }}</time><i/>UTC+8</span><span class="sky-pill product-pill"><Coin aria-hidden="true"/>L · 全国城市列表</span><button class="sky-pill data-pill" @click="dialog = 'source'"><b>F</b>合成演示</button><button class="sky-pill user-pill" :aria-label="`${store.canGovern ? '治理账号' : '观察账号'}，演示账号与权限设置`" @click="emit('settings')"><span class="user-orb"><UserFilled aria-hidden="true"/></span><span>{{ store.canGovern ? '治理账号' : '观察账号' }}</span><ArrowDown aria-hidden="true"/></button></div>
      </header>
      <div class="sky-metrics" aria-label="合成观测摘要"><SkyMetric v-for="item in metrics" :key="item.label" v-bind="item"/></div>
      <div class="sky-primary">
        <AtlasMap :metric="metric" :city="query('city')" @select="setQuery({ city: $event })" @metric="setQuery({ metric: $event === 'AQI' ? undefined : $event })" @detail="dialog = $event"/>
        <section class="sky-glass ranking-panel" aria-labelledby="rank-heading"><div class="stat-head"><h2 id="rank-heading">空气质量排名</h2><span class="top-five">TOP 5</span></div>
          <div class="segmented rank-switch" role="group" aria-label="排名顺序"><button :aria-pressed="!low" @click="setQuery({ rank: undefined })">污染较重</button><button :aria-pressed="low" @click="setQuery({ rank: 'low' })">空气较好</button></div>
          <div class="rank-grid rank-labels"><span>次序</span><span>城市 / 省份</span><span>AQI</span><span class="rank-primary">首要污染物</span></div>
          <ol class="rank-items"><li v-for="(city, index) in rankList" :key="city.name"><button class="rank-grid" aria-description="查看该城市的同刻记录" @click="dialog = city"><span class="rank-no">{{ String(index + 1).padStart(2, '0') }}</span><span class="rank-city">{{ city.name }}<small>{{ city.province }}</small></span><b class="aqi-num" :class="`g${city.grade}`">{{ city.aqi }}</b><span class="rank-primary">{{ city.primary }}</span></button></li></ol>
          <button class="rank-more sky-text-link" @click="list({ order: low ? 'asc' : undefined })">查看完整榜单<ArrowRight aria-hidden="true"/></button>
        </section>
        <div class="sky-insights"><section class="sky-glass distribution-panel"><div class="stat-head"><h2>AQI 等级分布</h2><small>同刻样本</small></div><div class="dist-body">
          <div class="ring-holder dist-ring"><ChartView :option="distributionChart" height="100%" label="AQI 各等级城市数，详见旁边的图例"/><div class="ring-center"><b>{{ cities.length }}</b><span>样本城市</span></div></div>
          <ul class="dist-legend"><li v-for="(item, i) in distribution" :key="item.name"><button aria-description="按此等级筛选城市列表" @click="list({ grade: String(i + 1) })"><i class="grade-dot" :style="{ '--dot': item.color }"/><span>{{ item.name }}</span><b>{{ item.count }}</b><small>{{ Math.round(item.count / cities.length * 100) }}%</small></button></li></ul>
        </div></section>
        <section class="sky-glass notices-panel"><div class="stat-head"><h2><Bell aria-hidden="true"/>观测提示</h2></div><button class="notice-row" @click="dialog = 'missing'"><Warning aria-hidden="true" class="notice-symbol"/>{{ missing.length }} 座名录城市本时刻缺报<ArrowRight aria-hidden="true"/></button><button class="notice-row" @click="dialog = 'source'"><InfoFilled aria-hidden="true" class="notice-symbol"/>查看样本来源与地图口径<ArrowRight aria-hidden="true"/></button></section></div>
      </div>
      <NationalStats :hours="hours" @window="setQuery({ hours: $event === 12 ? undefined : String($event) })" @ledger="dialog = 'trend'"/>
      <NationalTable ref="table" :query="query('q')" :province="provinces.includes(query('province')) ? query('province') : ''" :grade="validGrade" :order="query('order') === 'asc' ? 'asc' : 'desc'" :page="page" @filter="setQuery" @detail="dialog = $event"/>
    </main>
    <NationalDialog :content="dialog" :hours="hours" @close="dialog = null"/>
  </div>
</template>
