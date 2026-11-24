import type { EChartsOption, LineSeriesOption } from 'echarts'
import { objectName, scatterPoints, sourceTimezone, unit, type ResearchReport } from './researchMock'

const objectStyles = [
  { color: '#275ed5', kind: 'solid', symbol: 'circle' },
  { color: '#087d79', kind: 'dashed', symbol: 'diamond' },
  { color: '#a95a18', kind: 'dotted', symbol: 'triangle' },
] as const

const labels = new Intl.DateTimeFormat('sv-SE', {
  timeZone: sourceTimezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
export const localTime = (iso: string) => labels.format(new Date(iso))
export const percent = (value: number) => `${(value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
export const number = (value: number | string, max = 2) => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: max })
export const styleFor = (id: number) => objectStyles[id % 100 - 1] ?? objectStyles[0]

export interface ChartDatum { start: string; end: string; value: number | null }
export interface LineData { name: string; entity: number; pos: number; data: ChartDatum[] }
export interface Zoom { start: number; end: number }

export function timeSeries(report: ResearchReport): LineData[] {
  return report.columns.map(({ entity, pos }) => {
    let data: ChartDatum[]
    if (report.grain === 'day') {
      data = (report.buckets ?? []).filter((bucket) => bucket.pos === pos)
        .map(({ start, end, mean, valid_ratio }) => ({ start, end, value: valid_ratio >= 0.75 ? mean : null }))
    } else {
      data = report.chart.points.filter((point) => point.pos === pos).map((point) => ({
        start: point.at,
        end: new Date(Date.parse(point.at) + 3_600_000).toISOString(),
        value: point.quality === 'valid' ? point.value : null,
      }))
    }
    return { name: objectName(entity), entity, pos, data }
  })
}

export function lineChart(report: ResearchReport, lines: LineData[], zoom: Zoom): EChartsOption {
  const times = lines[0]?.data.map(({ start }) => localTime(start)) ?? []
  const zoomable = report.grain === 'hour' && times.length > 8
  const singleDay = report.grain === 'hour' && times.length <= 24
  const readingUnit = unit(report.selection.indicators[0])
  const series: LineSeriesOption[] = lines.map((line) => {
    const style = styleFor(line.entity)
    return {
      name: line.name, type: 'line', data: line.data.map(({ value }) => value),
      connectNulls: false, smooth: false,
      showSymbol: times.length <= 24, symbol: style.symbol, symbolSize: times.length < 3 ? 10 : 5.5,
      lineStyle: { color: style.color, width: 2.2, type: style.kind },
      itemStyle: { color: style.color, borderWidth: 1, borderColor: '#ffffff' },
      emphasis: { focus: 'series', itemStyle: { borderWidth: 2 } },
    }
  })
  return {
    animation: false,
    grid: { left: 13, right: 15, top: 23, bottom: 18, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: '#7489a4', type: 'dashed' } },
      valueFormatter: (value) => typeof value === 'number' ? `${number(value)} ${readingUnit}` : '缺测',
    },
    xAxis: {
      type: 'category', data: times, boundaryGap: times.length < 3,
      axisLabel: {
        hideOverlap: true, showMaxLabel: true,
        formatter: (value) => report.grain === 'day' ? value.slice(5, 10) : singleDay ? value.slice(11) : value.slice(5),
      },
    },
    yAxis: { type: 'value', min: 0, splitNumber: 4 },
    dataZoom: zoomable ? [{ type: 'inside', start: zoom.start, end: zoom.end, zoomOnMouseWheel: false, moveOnMouseWheel: false, moveOnMouseMove: false }] : [],
    series,
  }
}

function binLabel(lower: number | null, upper: number | null) {
  if (lower === null) return `<${upper}`
  return upper === null ? `≥${lower}` : `${lower}–${upper}`
}

export function distributionChart(report: ResearchReport): EChartsOption {
  const bins = report.distribution ?? []
  return {
    animation: false,
    grid: { left: 12, right: 10, top: 16, bottom: 12, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
    xAxis: { type: 'category', data: bins.map(({ lower, upper }) => binLabel(lower, upper)), axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', minInterval: 1, splitNumber: 3 },
    series: [{ name: '有效样本', type: 'bar', data: bins.map(({ n }) => n), barMaxWidth: 40, itemStyle: { color: '#4174dc', borderRadius: [3, 3, 0, 0] } }],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (value) => `${value} 条` },
  }
}

export function correlationChart(report: ResearchReport): EChartsOption {
  const [x, y] = report.selection.indicators
  return {
    animation: false,
    grid: { left: 13, right: 17, top: 23, bottom: 30, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
    xAxis: { type: 'value', name: `${x} (${unit(x)})`, nameLocation: 'center', nameGap: 29, axisLine: { show: true }, scale: true },
    yAxis: { type: 'value', splitNumber: 4, scale: true },
    series: [{ name: `${x} / ${y}`, type: 'scatter', symbolSize: 10,
      data: scatterPoints(report).map(({ x, y, at }) => ({ value: [x, y], name: localTime(at) })),
      itemStyle: { color: '#275ed5', opacity: 0.72, borderColor: '#173869', borderWidth: 0.6 },
    }],
    tooltip: { trigger: 'item', valueFormatter: (value) => Array.isArray(value) ? `${x} ${value[0]} ${unit(x)} · ${y} ${value[1]} ${unit(y)}` : String(value) },
  }
}
