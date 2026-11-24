<script setup lang="ts">
import { computed, ref } from 'vue'
import { Aim, Minus, Plus } from '@element-plus/icons-vue'
import relief from './assets/atlas-relief.webp'
import { cities, grades, gradeColors, metricGrade, metricLabels, metricText, ranges, type CitySample, type Metric } from './snapshot'
import { points } from './mapPoints'

const props = defineProps<{ metric: Metric; city: string }>()
const emit = defineEmits<{ metric: [value: Metric]; select: [name: string]; detail: [city: CitySample] }>()
const hasMissing = computed(() => points.some(city => metricGrade(city, props.metric) == null))
const labels = new Set(['北京', '哈尔滨', '乌鲁木齐', '拉萨', '成都', '广州', '上海'])
const zoom = ref(1)
const defaultPoint = points.find(row => row.name === '北京')!
const active = computed(() => points.find(row => row.name === props.city) ?? defaultPoint)
const hovered = ref<(typeof points)[number] | null>(null)
const pointer = computed(() => hovered.value || active.value)
const svg = ref<SVGSVGElement>()
const camera = ref({ x: 0, y: 0 })
// Trim the empty atlas margins, keeping the map and sample points in one coordinate system.
const frame = { width: 570, top: 8, height: 314 }
const callout = { width: 114, height: 37 }
const left = 58
const landTransform = computed(() => `translate(${left + camera.value.x} ${camera.value.y}) scale(${zoom.value})`)
const tipPosition = computed(() => {
  const { x, y } = pointer.value.point
  return {
    x: Math.max(4, Math.min(frame.width - callout.width - 4, left + camera.value.x + x * zoom.value + 8)),
    y: Math.max(frame.top + 2, Math.min(frame.top + frame.height - callout.height - 2, camera.value.y + y * zoom.value - callout.height - 4)),
  }
})
function reset() { zoom.value = 1; camera.value = { x: 0, y: 0 }; hovered.value = null }
function changeZoom(delta: number) {
  const next = Math.max(1, Math.min(2.5, zoom.value + delta))
  if (next === 1) { reset(); return }
  const center = active.value.point
  camera.value = constrain({ x: (1 - next) * center.x, y: (1 - next) * center.y }, next)
  zoom.value = next
}
function constrain(position: { x: number; y: number }, scale: number) {
  return { x: Math.min(0, Math.max(500 * (1 - scale), position.x)), y: Math.min(0, Math.max(322 * (1 - scale), position.y)) }
}
let drag: { x: number; y: number; startX: number; startY: number } | null = null
function beginPan(event: PointerEvent) {
  if (zoom.value <= 1 || (event.target as Element).closest('[role="button"]')) return
  drag = { x: event.clientX, y: event.clientY, startX: camera.value.x, startY: camera.value.y }
  svg.value?.setPointerCapture(event.pointerId)
}
function pan(event: PointerEvent) {
  const matrix = svg.value?.getScreenCTM()
  if (!drag || !matrix) return
  camera.value = constrain({ x: drag.startX + (event.clientX - drag.x) / matrix.a, y: drag.startY + (event.clientY - drag.y) / matrix.d }, zoom.value)
}
function choose(city: (typeof points)[number], open = false) {
  hovered.value = null
  if (city !== active.value) emit('select', city.name)
  if (open) emit('detail', city)
}
function revealFocus(event: FocusEvent, city: (typeof points)[number]) {
  if (!(event.currentTarget as Element).matches(':focus-visible')) return
  hovered.value = null
  const box = svg.value?.viewBox.baseVal
  if (!box || zoom.value === 1) return
  const { x, y } = city.point
  const cx = left + camera.value.x + x * zoom.value
  const cy = camera.value.y + y * zoom.value
  const margin = (city.radius + 4) * zoom.value
  if (cx < margin || cx > box.width - margin || cy < box.y + margin || cy > box.y + box.height - margin) {
    camera.value = constrain({ x: (1 - zoom.value) * x, y: (1 - zoom.value) * y }, zoom.value)
  }
}
function keyPoint(event: KeyboardEvent, index: number) {
  const movement: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  if (!(event.key in movement)) return
  event.preventDefault()
  const next = (index + movement[event.key] + points.length) % points.length
  choose(points[next])
  if (zoom.value > 1) camera.value = constrain({ x: (1 - zoom.value) * points[next].point.x, y: (1 - zoom.value) * points[next].point.y }, zoom.value)
  svg.value?.querySelector<SVGGElement>(`[data-point="${next}"]`)?.focus()
}
</script>
<template>
  <section class="sky-glass sky-map" aria-labelledby="map-heading">
    <div class="atlas-heading"><h2 id="map-heading">全国空气质量分布</h2><div class="segmented metric-switch" role="group" aria-label="地图指标"><button v-for="(label, key) in metricLabels" :key="key" :aria-pressed="metric === key" :aria-label="key === 'AQI' ? 'AQI' : `${label} 等级`" @click="emit('metric', key)">{{ label }}</button></div></div>
    <div class="atlas-stage">
      <div class="atlas-north" aria-hidden="true"><b>N</b><svg viewBox="0 0 18 25"><path d="m9 1 6 20-6-4-6 4z" fill="#39728c"/><path d="M9 1v16l6 4z" fill="#a2c6d4"/></svg></div>
      <svg ref="svg" class="atlas-svg" :class="{ 'atlas-zoomed': zoom > 1 }" :viewBox="`0 ${frame.top} ${frame.width} ${frame.height}`" role="group" aria-label="城市样点示意图，方向键选择城市，回车查看记录" @pointerdown="beginPan" @pointermove="pan" @pointerup="drag = null" @pointercancel="drag = null">
        <g :transform="landTransform"><image :href="relief" x="0" y="0" width="500" height="322" class="atlas-relief"/>
          <g v-for="(city, index) in points" :key="city.name" class="map-pin" :data-point="index"
            :transform="`translate(${city.point.x} ${city.point.y})`" role="button" :tabindex="city === active ? 0 : -1"
            :aria-pressed="city === active" :aria-label="`${city.name}，${metricText(city, metric)}，查看记录`"
            @focus="revealFocus($event, city)" @keydown="keyPoint($event, index)" @keydown.enter.prevent="choose(city, true)" @keydown.space.prevent="choose(city, true)"
            @click="choose(city, true)" @pointerenter="hovered = city" @pointerleave="hovered = null">
            <polygon class="pin-target" :points="city.target" fill="transparent"/>
            <circle class="pin-halo" :r="city.radius + 2.5" :fill="gradeColors[(metricGrade(city, metric) || 0) - 1] || '#8999a5'"/>
            <circle class="pin-dot" :r="city.radius" :fill="gradeColors[(metricGrade(city, metric) || 0) - 1] || '#8999a5'"/>
            <circle v-if="city === active" class="pin-rim" :r="city.radius + 3.5"/>
            <circle v-if="active.name === city.name || hovered?.name === city.name" class="pin-ring" :r="city.radius + 3.5"/>
            <text v-if="labels.has(city.name) && pointer.name !== city.name" :x="city.name === '广州' ? -8 : 8" :text-anchor="city.name === '广州' ? 'end' : 'start'" :y="city.name === '广州' ? 9 : -4" class="atlas-city-name">{{ city.name }}</text>
          </g>
        </g>
        <g class="map-callout" :transform="`translate(${tipPosition.x} ${tipPosition.y})`" aria-hidden="true"><rect :width="callout.width" :height="callout.height" rx="7"/><text x="9" y="15">{{ pointer.name }}</text><text x="9" y="30">{{ metricText(pointer, metric) }}</text></g>
      </svg>
      <div class="atlas-tools" role="group" aria-label="地图视图"><button aria-label="放大地图" title="放大地图" :disabled="zoom === 2.5" @click="changeZoom(.5)"><Plus aria-hidden="true"/></button><button aria-label="缩小地图" title="缩小地图" :disabled="zoom === 1" @click="changeZoom(-.5)"><Minus aria-hidden="true"/></button><button aria-label="复位地图" title="复位地图" @click="reset"><Aim aria-hidden="true"/></button></div>
      <div class="atlas-legend"><strong>{{ metric === 'AQI' ? 'AQI 等级' : metricLabels[metric] + ' 等级' }}</strong><span v-for="(grade, i) in grades" :key="grade"><i class="grade-dot" :style="{ '--dot': gradeColors[i] }"/>{{ metric === 'AQI' ? grade : `${i + 1} 级` }} <small v-if="metric === 'AQI'">{{ ranges[i] }}</small></span><span v-if="hasMissing"><i class="grade-dot" style="--dot: #8999a5"/>缺测</span></div>
    </div>
    <div class="map-caption"><span><i class="point-key"/>{{ points.length }}/{{ cities.length }} 城已定位，{{ cities.length - points.length }} 城仅列于表格</span><span>Natural Earth · 原型示意底图</span></div>
  </section>
</template>
