<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import { initChart } from './chartTheme'

const props = withDefaults(defineProps<{ option: EChartsOption; label: string; height?: number | string }>(), { height: 224 })
const emit = defineEmits<{ point: [point: { seriesIndex: number; dataIndex: number }] }>()
const host = ref<HTMLDivElement>()
let chart: ReturnType<typeof initChart> | undefined
let observer: ResizeObserver | undefined
function render() { chart?.setOption(props.option, { notMerge: true }) }
onMounted(() => {
  if (!host.value) return
  chart = initChart(host.value)
  chart.on('click', (event) => {
    if (typeof event.seriesIndex === 'number' && typeof event.dataIndex === 'number') {
      emit('point', { seriesIndex: event.seriesIndex, dataIndex: event.dataIndex })
    }
  })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(host.value)
  render()
  void document.fonts.ready.then(() => chart?.resize())
})
watch(() => props.option, render, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); chart = undefined })
</script>
<template><div ref="host" class="chart-surface" role="img" :aria-label="label" :style="{ height: typeof height === 'number' ? `${height}px` : height }" /></template>
