import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import type { EChartsOption, LinearGradientObject } from 'echarts'
import { distribution, hourly, cities, missing } from './snapshot'

use([PieChart])
const textStyle = { fontFamily: 'InterPreview, "Noto Sans SC Variable", sans-serif', color: '#3c5b72', fontSize: 11 }
const tooltip = {
  confine: true, transitionDuration: 0, className: 'sky-chart-tip',
  backgroundColor: '#eff7fc', borderWidth: 1, borderColor: '#92b2c6', padding: [6, 10],
  textStyle: { ...textStyle, color: '#19394f', fontSize: 12 },
}
function fade(light: string, deep: string, vertical = false): LinearGradientObject {
  return { type: 'linear', x: 0, y: 0, x2: vertical ? 0 : 1, y2: vertical ? 1 : 0, colorStops: [{ offset: 0, color: light }, { offset: 1, color: deep }] }
}
type Slice = { value: number; name: string; itemStyle: { color: string | LinearGradientObject } }
function ring(parts: Slice[], thin = false): EChartsOption {
  return { animation: false, textStyle, tooltip: { ...tooltip, trigger: 'item', formatter: '{b} · {c} 城' }, series: [{ type: 'pie', radius: thin ? ['70%', '87%'] : ['60%', '89%'], center: ['50%', '50%'], label: { show: false }, labelLine: { show: false }, emphasis: { scaleSize: 2 }, itemStyle: { borderWidth: 1.3, borderColor: '#f1fbfc' }, data: parts.filter(p => p.value > 0) }] }
}
const ringTints = [['#93e795', '#43c764'], ['#ffe890', '#f7cb52'], ['#ffbd77', '#f48f44'], ['#ff9990', '#ea6865'], ['#d690ad', '#a83a6d'], ['#a8667c', '#873151']]
export const distributionChart = ring(distribution.map((group, index) => ({ name: group.name, value: group.count, itemStyle: { color: fade(ringTints[index][0], ringTints[index][1]) } })))
export const coverageChart = ring([
  { name: '已到报', value: cities.length, itemStyle: { color: fade('#28a9c3', '#297ad2', true) } },
  { name: '尚缺报', value: missing.length, itemStyle: { color: '#c5dce7' } },
], true)
export function lineChart(hours: number): EChartsOption {
  const data = hourly.slice(-hours)
  return {
    animation: false, textStyle, tooltip: { ...tooltip, trigger: 'axis', formatter: '{b} · {c} 城', axisPointer: { type: 'line', lineStyle: { color: '#6591ac', width: 1, type: 'dashed' } } },
    grid: { left: 26, right: 20, top: 10, bottom: 24, outerBoundsMode: 'none' },
    xAxis: { type: 'category', boundaryGap: false, data: data.map(d => d.time), axisLabel: { ...textStyle, margin: 8, fontSize: 10, hideOverlap: true, showMaxLabel: true }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
    yAxis: { type: 'value', min: 30, max: 50, interval: 5, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...textStyle, margin: 7, fontSize: 10 }, splitLine: { lineStyle: { color: '#cedeeb', width: 1, type: 'solid' } } },
    series: [{ type: 'line', name: '到报城市', data: data.map(d => d.count), showAllSymbol: true, symbol: 'circle', symbolSize: 6, lineStyle: { width: 2, color: '#1673bc' }, itemStyle: { color: '#f5fcff', borderColor: '#1673bc', borderWidth: 1.6 }, areaStyle: { color: fade('#80bce766', '#dceefa08', true) } }],
  }
}
