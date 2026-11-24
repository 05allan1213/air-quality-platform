<script setup lang="ts">
import { computed } from 'vue'
import { ElDialog } from 'element-plus'
import { Aim, ArrowRight, InfoFilled, Location, MapLocation } from '@element-plus/icons-vue'
import { expected, grades, hourly, metricLabels, missing, snapshot, type CitySample } from './snapshot'

export type HomeDialog = CitySample | 'source' | 'missing' | 'trend'
const props = defineProps<{ content: HomeDialog | null; hours: number }>()
const emit = defineEmits<{ close: [] }>()
const city = computed(() => typeof props.content === 'object' ? props.content : null)
const titles = { source: '数据与地图说明', missing: '本时刻缺报城市', trend: '样本到报明细' }
const title = computed(() => typeof props.content === 'string' ? titles[props.content] : `${city.value?.name ?? ''} · 同刻观测详情`)
</script>
<template>
  <ElDialog :model-value="content !== null" :title="title" width="472px" class="sky-dialog" modal-class="sky-scrim" transition="sky-sheet"
    align-center append-to-body destroy-on-close @update:model-value="emit('close')">
    <template v-if="city">
      <div class="detail-reading"><strong>{{ city.aqi }}</strong><span>AQI</span>
        <span class="grade-label detail-grade" :class="`g${city.grade}`">{{ grades[city.grade - 1] }}</span>
      </div>
      <div class="detail-basis"><span>F 合成演示 · L 全国城市列表</span><time :datetime="snapshot.observedAt">{{ snapshot.date }} {{ snapshot.hour }} UTC+8</time></div>
      <dl class="sky-detail-list">
        <div><dt>所属地域</dt><dd>{{ city.province }}</dd></div>
        <div><dt>同刻排名</dt><dd>第 {{ city.rank }} 名</dd></div>
        <div><dt>首要污染物</dt><dd>{{ city.primary }}</dd></div>
        <div v-for="key in (['PM25','PM10','O3'] as const)" :key="key"><dt>{{ metricLabels[key] }} 等级</dt><dd>{{ city.levels[key] === null ? '缺测' : `${city.levels[key]} 级` }}</dd></div>
        <div><dt>标准系列</dt><dd>HJ 633—2012</dd></div>
      </dl>
      <RouterLink v-if="city.name === '云汀市'" to="/city" class="sky-text-link city-deep">打开同城 C 产品详情<ArrowRight aria-hidden="true"/></RouterLink>
    </template>
    <div v-else-if="content === 'source'" class="source-notes">
      <p><span><b>F 合成数据 · L 产品语义</b>观测读数、到报台账与应到名录均为本地演示样本。排名比较同一时刻、同一标准的 AQI。</span><InfoFilled aria-hidden="true"/></p>
      <p><span><b>指标口径</b>污染物列展示样本来源给出的等级。到报率以本小时合成应到名录为分母。</span><Aim aria-hidden="true"/></p>
      <p><span><b>Natural Earth 公有领域底图</b>城市中心点与地形使用 WGS84 数据，经等距圆柱投影绘制。彩色点表示城市样本，地形底色表示地势。云汀市缺少定位依据，仅列于表中。正式地图仍待验收。</span><MapLocation aria-hidden="true"/></p>
    </div>
    <div v-else-if="content === 'missing'" class="missing-note">
      <p>{{ snapshot.date }} {{ snapshot.hour }}（UTC+8）的 F 应到名录有 {{ expected }} 城，其中下列 {{ missing.length }} 城尚无本时刻样本。</p>
      <ul><li v-for="name in missing" :key="name"><Location aria-hidden="true"/>{{ name }}</li></ul>
    </div>
    <template v-else-if="content === 'trend'">
      <p class="dialog-basis">{{ snapshot.date }} · UTC+8 · F 逐小时到报台账</p>
      <table class="ledger-table"><thead><tr><th scope="col">观测时刻</th><th scope="col">到报城市数</th></tr></thead>
        <tbody><tr v-for="cell in hourly.slice(-hours)" :key="cell.time"><td>{{ cell.time }}</td><td>{{ cell.count }} 城</td></tr></tbody>
      </table>
    </template>
  </ElDialog>
</template>
