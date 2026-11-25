<script setup lang="ts">
import { qualities, rowValue, windows, type CatalogObject, type ObservationRow } from './queryContract'

defineProps<{
  rows: ObservationRow[]
  objectLabel: (row: ObservationRow) => string
  tableLabel: string
  allowExact?: boolean
  shape?: CatalogObject['shape']
}>()
const emit = defineEmits<{ inspect: [row: ObservationRow, event: MouseEvent] }>()
</script>

<template>
  <div class="query-table-scroll" :aria-label="`${tableLabel}（可水平滚动）`" role="region" tabindex="0">
    <table :aria-label="tableLabel">
      <thead>
        <tr>
          <th scope="col">对象 / 指标</th><th scope="col">数值 / 单位</th><th scope="col">质量</th>
          <th scope="col">观测时标</th><th scope="col">采集时刻</th><th scope="col">来源 / 口径</th><th scope="col">修订</th>
          <th v-if="allowExact" scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key ?? row.revision_id">
          <th scope="row">{{ objectLabel(row) }} <small>{{ row.indicator }}</small></th>
          <td class="numeric">{{ rowValue(row) }} <small>{{ row.unit }}</small><small v-if="row.raw_unit && row.raw_unit !== row.unit">原单位 {{ row.raw_unit }}</small></td>
          <td><span class="quality" :class="`quality-${row.quality}`">{{ qualities[row.quality] || row.quality }}</span></td>
          <td>
            <template v-if="row.local_report_date">{{ row.local_report_date }} <small>源地方报告日</small></template>
            <template v-else>{{ row.observed_at || '未知' }}</template>
            <small v-if="row.source_timezone">源时区 {{ row.source_timezone }}</small>
            <small v-if="row.source_time && row.source_time !== row.observed_at">源标记 {{ row.source_time }}</small>
            <small v-if="row.source_local_date && !row.local_report_date">源地方日 {{ row.source_local_date }}</small>
          </td>
          <td>
            {{ row.fetched_at || '未知' }}
            <small v-if="row.fetched_at_basis === 'contributing_batch'">贡献批次</small>
            <small v-if="row.execution_started_at">执行开始 {{ row.execution_started_at }}</small>
            <small v-if="row.source_event_at">来源事件 {{ row.source_event_at }}</small>
            <small v-if="row.source_status && row.source_status !== 'unknown'">来源状态 {{ row.source_status }}</small>
          </td>
          <td>
            {{ shape || (row.local_report_date ? 'D' : '') }} {{ row.source }}<small>产品 #{{ row.product }} · {{ windows[row.window_definition]?.label || row.window_definition }}</small>
            <small>{{ row.standard_series === 'not_applicable' ? '标准不适用' : row.standard_series === 'unknown' ? '标准未知' : row.standard_series }}</small>
            <small v-if="row.source_description">{{ row.source_description }}</small>
            <small v-if="row.attribution">{{ row.attribution }}</small>
            <small v-if="row.rights_attributions?.length">{{ row.rights_attributions.join('；') }}</small>
          </td>
          <td class="numeric">#{{ row.revision ?? row.revision_id ?? '—' }}</td>
          <td v-if="allowExact"><button v-if="row.observed_at" class="btn" type="button" @click="emit('inspect', row, $event)">精确读取</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.query-table-scroll { max-width: 100%; min-width: 0; overflow: auto; border: 1px solid var(--aq-border); border-radius: 9px; }
.query-table-scroll:focus-visible { outline: 2px solid var(--aq-focus); outline-offset: 2px; }
table { width: 100%; min-width: 780px; border-collapse: collapse; color: var(--aq-text); font-size: 14px; text-align: left; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--aq-border); vertical-align: top; overflow-wrap: anywhere; }
th { font-weight: 620; }
thead { background: var(--aq-content-strong); }
tbody tr:nth-child(even) { background: var(--aq-content-strong); }
tbody tr:last-child :is(th, td) { border-bottom: 0; }
th:first-child { min-width: 115px; }
th:nth-child(2) { min-width: 82px; }
th:nth-child(3) { min-width: 62px; }
th:last-child .btn, td .btn { white-space: nowrap; }
small { display: block; color: var(--aq-muted); font-weight: 450; }
.numeric { font-variant-numeric: tabular-nums; }
.quality { display: inline-block; padding: 2px 6px; border-radius: 5px; background: var(--aq-content-strong); color: var(--aq-secondary); white-space: nowrap; }
.quality-valid { background: var(--aq-success-bg); color: var(--aq-success); }
.quality-censored, .quality-suspect { background: var(--aq-warning-bg); color: var(--aq-warning); }
.quality-invalid, .quality-disputed { background: var(--aq-danger-bg); color: var(--aq-danger); }
</style>
