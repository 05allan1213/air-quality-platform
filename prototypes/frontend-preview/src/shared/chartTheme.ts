import { use, registerTheme, init } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, ScatterChart, BoxplotChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, MarkPointComponent, AriaComponent } from 'echarts/components'

use([CanvasRenderer, LineChart, BarChart, ScatterChart, BoxplotChart, GridComponent,
  LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, MarkPointComponent, AriaComponent])
const axis = {
  axisLabel: { color: '#56677a', fontSize: 11, margin: 11 }, axisTick: { show: false },
  axisLine: { lineStyle: { color: '#ccd6df' } },
  splitLine: { lineStyle: { color: '#e4eaf0', type: 'dashed' } },
}
registerTheme('observation', {
  color: ['#275ed5', '#087d79', '#a95a18', '#71488d', '#47566b'],
  animation: false,
  textStyle: { fontFamily: 'InterPreview, "Noto Sans SC Variable", system-ui, sans-serif', color: '#35465a' },
  categoryAxis: axis, valueAxis: axis,
  tooltip: { confine: true, backgroundColor: '#fff', borderColor: '#c8d3df', textStyle: { color: '#1b2b41', fontSize: 13 } },
})
export const initChart = (node: HTMLElement) => init(node, 'observation', { renderer: 'canvas' })
