<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, usePrivateRead } from '../api/client'
import { label, timeText, type Cell, type WindowTrace } from './qualityContract'

const props = defineProps<{ id: number; product: number; synthetic: boolean; revision: number }>()
const emit = defineEmits<{
  events: [window: number, run?: string]
  batch: [batch: number, receipt?: number, keyType?: 'city' | 'station']
}>()
const { data, error, busy, run, clear } = usePrivateRead<WindowTrace>()
const cell = ref<Cell | null>(null)
const runFilter = ref('')
const indicator = ref('')
const cellOffset = ref<number | undefined>()
const runOffset = ref<number | undefined>()
const trace = computed(() => data.value)
const items = computed(() => trace.value?.items?.filter(item => item.item_key === cell.value?.item_key) ?? [])
const receipts = computed(() => trace.value?.receipts?.filter(receipt => items.value.some(item => item.id === receipt.item_id)) ?? [])
function read(after?: number, runAfter?: number) {
  cell.value = null
  cellOffset.value = after
  runOffset.value = runAfter
  return run(async signal => {
    const result = await api.get<WindowTrace>(`admin/collection-windows/${props.id}/`, {
      indicator: indicator.value || undefined, run: runFilter.value || undefined, after, run_after: runAfter,
    }, signal)
    if (result.window_id !== props.id || (result.coverage !== null && (result.product !== props.product || result.synthetic !== props.synthetic))) {
      // The window path has no domain selector. Never render a cross-domain response.
      throw new Error('Window context mismatch')
    }
    return result
  })
}
watch(() => [props.id, props.product, props.synthetic, props.revision], () => {
  clear()
  indicator.value = ''
  runFilter.value = ''
  void read()
}, { immediate: true })
</script>

<template>
  <section class="panel quality-section" aria-labelledby="quality-trace-title" :aria-busy="busy">
    <div class="quality-bar">
      <div><h2 id="quality-trace-title" tabindex="-1">窗口覆盖与缺口</h2><p>应到、实到与有效是不同口径；这里显示窗口范围，不是一次 Run 的产出。</p></div>
      <div class="quality-actions"><button class="btn" type="button" :disabled="busy" @click="read()">复读窗口</button><button v-if="trace?.coverage" class="btn" type="button" @click="emit('events', id, runFilter || undefined)">筛选本窗事件</button></div>
    </div>
    <p v-if="busy" role="status">正在核验窗口投影…</p>
    <p v-else-if="error" class="status status-error" role="alert">无法读取当前窗口。请核对交接范围与现有授权。</p>
    <template v-else-if="trace">
      <p v-if="!trace.coverage" role="status">{{ label(trace.state) }}：没有可用的覆盖投影。</p>
      <template v-else>
        <p class="quality-context">窗口 {{ id }} · {{ timeText(trace.target_start) }} 至 {{ timeText(trace.target_end) }}（右端不含） · {{ label(trace.state) }} · {{ label(trace.result) }}</p>
        <dl class="quality-meta coverage" data-testid="window-coverage"><div><dt>E 应到</dt><dd>{{ trace.coverage.E ?? '未知' }}</dd></div><div><dt>O 实到</dt><dd>{{ trace.coverage.O }}</dd></div><div><dt>V 有效</dt><dd>{{ trace.coverage.V }}</dd></div></dl>
        <p v-if="trace.state === 'partially_authorized'" class="status status-warning">仅展示获准部分，不推断整窗结果或各次 Run 的状态。</p>
        <div class="quality-scroll" tabindex="0" role="region" aria-label="各指标覆盖表"><table><caption>各指标覆盖</caption><thead><tr><th scope="col">指标</th><th scope="col">E 应到</th><th scope="col">O 实到</th><th scope="col">V 有效</th><th scope="col">范围</th></tr></thead><tbody><tr v-for="(count, key) in trace.coverage.by_indicator" :key="key"><th scope="row">{{ key }}</th><td>{{ count.E ?? '未知' }}</td><td>{{ count.O }}</td><td>{{ count.V }}</td><td><button v-if="indicator !== key" class="btn quality-small" type="button" @click="indicator = key; read()">仅看 {{ key }}</button><button v-else class="btn quality-small" type="button" @click="indicator = ''; read()">清除指标筛选</button></td></tr></tbody></table></div>
        <div v-if="trace.cells?.length" class="quality-scroll" tabindex="0" role="region" aria-label="获准单元与缺口表"><table><caption>获准单元与缺口</caption><thead><tr><th scope="col">对象</th><th scope="col">观测时刻</th><th scope="col">指标</th><th scope="col">实到 / 有效</th><th scope="col">原因</th><th scope="col">证据</th></tr></thead><tbody><tr v-for="slot in trace.cells" :key="slot.item_key"><th scope="row">{{ slot.object_code }}</th><td>{{ timeText(slot.observed_at) }}</td><td>{{ slot.indicator }}</td><td>{{ slot.received ? '实到' : '未到' }} / {{ slot.valid ? '有效' : '未生效' }}<small v-if="slot.expected === false">非应到项</small></td><td>{{ label(slot.reason, '无') }}</td><td><button class="btn quality-small" type="button" :aria-pressed="cell?.item_key === slot.item_key" @click="cell = slot">定位子项</button></td></tr></tbody></table></div>
        <div v-if="cell" class="quality-chain" aria-label="当前单元链路">
          <h3>{{ cell.object_code }} · {{ cell.indicator }} · 子项与回执</h3>
          <p v-if="!items.length">当前授权页未提供关联子项。</p>
          <dl v-else class="quality-meta"><div v-for="item in items" :key="item.id"><dt>子项 {{ item.id }} · {{ label(item.status) }}</dt><dd>{{ label(item.reason, '无明细') }} · Run {{ item.run_id }}</dd><dd class="quality-actions"><button class="btn quality-small" type="button" @click="emit('events', id, item.run_id)">查 Run 事件</button><button v-if="item.batch_id" class="btn quality-small" type="button" @click="emit('batch', item.batch_id)">读批次 {{ item.batch_id }}</button></dd></div></dl>
          <div v-for="entry in receipts" :key="entry.id" class="quality-actions">
            <p>回执 {{ entry.id }} · 批次 {{ entry.batch_id }} · {{ label(entry.outcome) }} · 修订 {{ entry.revision.id ?? '未披露' }}</p>
            <button class="btn quality-small" type="button" @click="emit('batch', entry.batch_id, entry.id, entry.revision.id == null ? undefined : entry.revision.type)">查关联批次与回执</button>
          </div>
          <p v-if="trace.items_truncated || trace.receipts_truncated" class="status status-warning">子项或回执已截断，仅按当前获权页定位。</p>
        </div>
        <div v-if="trace.next_after != null || cellOffset != null" class="quality-actions">
          <button v-if="trace.next_after != null" type="button" class="btn" @click="read(trace.next_after, runOffset)">下一页单元</button><button v-if="cellOffset != null" type="button" class="btn" @click="read(undefined, runOffset)">首批单元</button>
        </div>
        <p v-if="runFilter" class="quality-context">当前限定 Run {{ runFilter }}；覆盖仍为当前业务窗口。 <button class="btn quality-small" type="button" @click="runFilter = ''; read()">查看全部 Run</button></p>
        <div class="quality-scroll" v-if="trace.runs?.length" tabindex="0" role="region" aria-label="各次运行状态表"><table><caption>各次 Run</caption><thead><tr><th scope="col">Run</th><th scope="col">执行</th><th scope="col">投递</th><th scope="col">结果</th><th scope="col">下钻</th></tr></thead><tbody><tr v-for="entry in trace.runs" :key="entry.id"><th scope="row">{{ entry.id }}</th><td>{{ label(entry.status) }}</td><td>{{ label(entry.dispatch) }}</td><td>{{ label(entry.result) }}<small v-if="entry.error_code">{{ label(entry.error_code) }}</small></td><td><div class="quality-actions"><button class="btn quality-small" type="button" @click="runFilter = entry.id; read()">仅看该 Run 子项</button><button class="btn quality-small" type="button" @click="emit('events', id, entry.id)">查 Run 事件</button></div></td></tr></tbody></table></div>
        <div v-if="trace.next_run_after != null || runOffset != null" class="quality-actions"><button v-if="trace.next_run_after != null" type="button" class="btn" @click="read(cellOffset, trace.next_run_after)">下一页 Run</button><button v-if="runOffset != null" type="button" class="btn" @click="read(cellOffset)">首批 Run</button></div>
      </template>
    </template>
  </section>
</template>
