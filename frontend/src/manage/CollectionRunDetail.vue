<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, Refresh, Warning } from '@element-plus/icons-vue'
import type { CollectionWindowProjection, RunEntry, RunItem, RunReceipt } from './collectionLedger'
import { label } from './collectionLedger'

const props = defineProps<{
  projection: CollectionWindowProjection | null
  selectedRunId?: string | null
  canManage?: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  'select-run': [runId: string]
  'redeliver': [run: RunEntry]
  'quality-open': [context: { window: number; run?: string; issue?: number; batch?: number }]
  'more-cells': [after: number]
  'more-runs': [after: number]
  'refresh': []
}>()

const projection = computed(() => props.projection)
const selectedRun = computed(() => projection.value?.runs?.find(run => run.id === props.selectedRunId) ?? null)
const canRedeliver = computed(() => {
  const run = selectedRun.value
  const target = projection.value?.target
  return Boolean(props.canManage && run && target && target.id === run.id
    && run.status === 'pending' && run.request_state === 'unsent' && run.dispatch === 'manual'
    && !run.next_retry_at && !target.owner_present && projection.value?.state === 'open')
})

function receiptsFor(item: RunItem): RunReceipt[] {
  return projection.value?.receipts?.filter(receipt => receipt.item_id === item.id) ?? []
}

function time(value: string | undefined) {
  if (!value) return '未披露'
  return `${value.replace('T', ' ').replace(/\.\d+(?=Z|[+-]|$)/, '')}`
}
</script>

<template>
  <section class="ledger-detail" aria-labelledby="ledger-detail-title" :aria-busy="loading">
    <header class="section-head ledger-detail-head">
      <div><h2 id="ledger-detail-title">窗口与运行证据</h2><p>窗口覆盖、单次运行、子项及回执按服务端投影分别呈现。</p></div>
      <button class="icon-button" type="button" :disabled="loading" aria-label="重新读取当前投影" title="重新读取" @click="emit('refresh')"><el-icon><Refresh /></el-icon></button>
    </header>
    <p v-if="loading" class="session-state" role="status">正在读取获权窗口投影…</p>
    <div v-else-if="!projection" class="ledger-empty">选择一个业务窗口或 Run 查看详情。</div>
    <template v-else>
      <div class="ledger-window-summary">
        <dl class="ledger-summary-grid">
          <div><dt>业务窗口</dt><dd>{{ projection.window_id }}</dd></div>
          <div><dt>状态</dt><dd>{{ label(projection.state) }}</dd></div>
          <div><dt>窗口结果</dt><dd>{{ projection.state === 'partially_authorized' || projection.coverage === null ? '未披露' : label(projection.result) }}</dd></div>
          <div><dt>E 应到</dt><dd>{{ projection.coverage?.E ?? '未知' }}</dd></div>
          <div><dt>O 实到</dt><dd>{{ projection.coverage?.O ?? '未披露' }}</dd></div>
          <div><dt>V 有效</dt><dd>{{ projection.coverage?.V ?? '未披露' }}</dd></div>
        </dl>
        <p v-if="projection.state === 'partially_authorized'" class="status status-warning">只显示当前获权部分，未披露的范围不计入推断。</p>
      </div>

      <section v-if="projection.coverage?.by_indicator" class="ledger-subsection" aria-labelledby="ledger-indicator-title">
        <div class="ledger-subsection-title"><h3 id="ledger-indicator-title">分指标覆盖</h3><span>来自当前窗口账本</span></div>
        <div class="ledger-scroll" tabindex="0" role="region" aria-label="分指标应到实到有效表">
          <table><caption>各指标 E / O / V</caption><thead><tr><th scope="col">指标</th><th scope="col">E 应到</th><th scope="col">O 实到</th><th scope="col">V 有效</th></tr></thead>
            <tbody><tr v-for="(counts, indicator) in projection.coverage.by_indicator" :key="indicator"><th scope="row">{{ indicator }}</th><td>{{ counts.E ?? '未知' }}</td><td>{{ counts.O }}</td><td>{{ counts.V }}</td></tr></tbody>
          </table>
        </div>
      </section>

      <section v-if="projection.cells?.length" class="ledger-subsection" aria-labelledby="ledger-cells-title">
        <div class="ledger-subsection-title"><h3 id="ledger-cells-title">应到单元与缺口</h3><span>{{ projection.cells.length }} 条当前页</span></div>
        <div class="ledger-scroll" tabindex="0" role="region" aria-label="获权应到单元表">
          <table><caption>窗口单元</caption><thead><tr><th scope="col">对象</th><th scope="col">观测时刻</th><th scope="col">指标</th><th scope="col">实到 / 有效</th><th scope="col">原因</th></tr></thead>
            <tbody><tr v-for="cell in projection.cells" :key="cell.item_key"><th scope="row">{{ cell.object_code }}</th><td>{{ time(cell.observed_at) }}</td><td>{{ cell.indicator }}</td><td>{{ cell.received ? '实到' : '未到' }} / {{ cell.valid ? '有效' : '未生效' }}</td><td>{{ label(cell.reason) }}</td></tr></tbody>
          </table>
        </div>
        <button v-if="projection.next_after != null" class="btn compact-btn" type="button" :disabled="loading" @click="emit('more-cells', projection.next_after)">读取下一页单元</button>
      </section>

      <section class="ledger-subsection" aria-labelledby="ledger-runs-title">
        <div class="ledger-subsection-title"><h3 id="ledger-runs-title">各次运行</h3><span>{{ projection.runs?.length ?? 0 }} 条当前页</span></div>
        <div v-if="projection.runs?.length" class="ledger-scroll" tabindex="0" role="region" aria-label="各次 Run 列表">
          <table><caption>各次 Run：本次状态与窗口状态分列</caption><thead><tr><th scope="col">Run</th><th scope="col">执行 / 投递</th><th scope="col">本次结果</th><th scope="col">操作</th></tr></thead>
            <tbody><tr v-for="run in projection.runs" :key="run.id" :class="{ selected: run.id === selectedRunId }">
              <th scope="row"><code>{{ run.id }}</code><small>{{ time(run.created_at) }} · {{ label(run.reason) }}</small></th>
              <td>{{ label(run.status) }} / {{ label(run.dispatch) }}<small v-if="run.request_state">请求：{{ label(run.request_state) }}</small><small v-if="run.error_code">原因：{{ label(run.error_code) }}</small></td>
              <td>{{ label(run.result) }}<small v-if="run.result === 'no_new'">本次运行未产生新观测；窗口结果仍见上方。</small></td>
              <td><button class="btn compact-btn" type="button" :aria-pressed="run.id === selectedRunId" @click="emit('select-run', run.id)">{{ run.id === selectedRunId ? '当前 Run' : '查看 Run' }}</button></td>
            </tr></tbody>
          </table>
        </div>
        <p v-else class="muted">当前投影没有可披露的 Run。</p>
        <button v-if="projection.next_run_after != null" class="btn compact-btn" type="button" :disabled="loading" @click="emit('more-runs', projection.next_run_after)">读取下一页 Run</button>
      </section>

      <section v-if="selectedRun" class="ledger-run-evidence" aria-labelledby="ledger-run-title">
        <header class="ledger-subsection-title"><div><h3 id="ledger-run-title">Run {{ selectedRun.id }}</h3><p>执行状态 {{ label(selectedRun.status) }} · 投递 {{ label(selectedRun.dispatch) }} · 本次结果 {{ label(selectedRun.result) }}</p></div>
          <button v-if="canRedeliver" class="btn compact-btn" type="button" @click="emit('redeliver', selectedRun)">重新投递</button>
        </header>
        <p v-if="projection.target" class="ledger-version">Run 控制版本 v{{ projection.target.control_version }} · 状态 {{ label(projection.target.state) }} · 请求 {{ label(projection.target.request_state) }}</p>
        <div v-if="projection.items?.length" class="ledger-scroll" tabindex="0" role="region" aria-label="所选 Run 子项">
          <table><caption>Run 子项和获准回执</caption><thead><tr><th scope="col">子项</th><th scope="col">状态 / 处理</th><th scope="col">批次</th><th scope="col">回执 / 修订</th><th scope="col">质量</th></tr></thead>
            <tbody><tr v-for="item in projection.items" :key="item.id">
              <th scope="row">{{ item.id }}<small>{{ item.item_key }}</small></th>
              <td>{{ label(item.status) }} / {{ item.accepted ? '已接受' : '未接受' }}<small v-if="item.reason">{{ label(item.reason) }}</small></td>
              <td>{{ item.batch_id ?? '无' }}</td>
              <td><template v-if="receiptsFor(item).length"><span v-for="receipt in receiptsFor(item)" :key="receipt.id" class="ledger-receipt">#{{ receipt.id }} · {{ label(receipt.outcome) }} · {{ receipt.revision.type }} {{ receipt.revision.id ?? '未披露' }}<small>批次 {{ receipt.batch_id }}</small></span></template><span v-else>无关联回执</span></td>
              <td><button class="btn compact-btn" type="button" @click="emit('quality-open', { window: projection.window_id, run: selectedRun.id, batch: item.batch_id ?? undefined })">质量入口</button></td>
            </tr></tbody>
          </table>
        </div>
        <p v-else class="muted">当前授权投影没有可披露的子项。</p>
        <p v-if="projection.items_truncated || projection.receipts_truncated" class="status status-warning"><el-icon><Warning /></el-icon> 当前子项或回执投影已截断，仅展示已返回的授权记录。</p>
        <div v-if="projection.quality_issues?.length" class="ledger-issues">
          <h4>关联质量事件</h4><ul><li v-for="issue in projection.quality_issues" :key="issue.id"><span>{{ label(issue.severity) }} · {{ label(issue.status) }} · {{ label(issue.code) }}</span><button class="btn compact-btn" type="button" @click="emit('quality-open', { window: projection.window_id, run: selectedRun.id, issue: issue.id })">打开质量事件 {{ issue.id }}</button></li></ul>
        </div>
        <p v-else-if="projection.quality_issues" class="muted">当前 Run 没有可披露的质量事件。</p>
      </section>
      <p v-else class="muted ledger-select-run"><el-icon><ArrowDown /></el-icon>选择一条 Run 查看本次子项、回执和质量事件。</p>
    </template>
  </section>
</template>

<style scoped>
.ledger-detail { min-width: 0; overflow-wrap: anywhere; }
.ledger-detail-head { align-items: center; margin-bottom: 14px; }
.ledger-detail-head h2 { margin-bottom: 3px; }
.ledger-detail-head p { font-size: 13px; }
.ledger-empty { display: grid; min-height: 180px; place-items: center; color: var(--aq-muted); }
.ledger-window-summary { padding: 12px 0 14px; border-bottom: 1px solid var(--aq-border); }
.ledger-summary-grid { display: grid; grid-template-columns: repeat(6, minmax(75px, 1fr)); gap: 8px; margin: 0; }
.ledger-summary-grid div { min-width: 0; padding: 8px 10px; background: rgb(246 251 254 / 68%); }
.ledger-summary-grid dt, .ledger-version { color: var(--aq-muted); font-size: 12px; }
.ledger-summary-grid dd { margin: 3px 0 0; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.ledger-subsection { margin-top: 17px; }
.ledger-subsection-title { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; margin-bottom: 7px; }
.ledger-subsection-title h3 { margin: 0; font-size: 15px; }
.ledger-subsection-title > span { color: var(--aq-muted); font-size: 12px; }
.ledger-subsection-title p { margin: 3px 0 0; font-size: 13px; }
.ledger-scroll { overflow: auto; max-width: 100%; border: 1px solid var(--aq-border); border-radius: 6px; }
.ledger-scroll table { width: 100%; border-collapse: collapse; font-size: 13px; }
.ledger-scroll caption { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
.ledger-scroll th, .ledger-scroll td { min-width: 86px; padding: 8px; border-bottom: 1px solid var(--aq-border); text-align: left; vertical-align: top; }
.ledger-scroll th { color: var(--aq-secondary); font-size: 12px; white-space: nowrap; }
.ledger-scroll tbody tr.selected { background: #e8f4fc; }
.ledger-scroll small, .ledger-receipt small { display: block; margin-top: 3px; color: var(--aq-muted); font-size: 11px; }
.ledger-scroll code { display: block; max-width: 210px; overflow-wrap: anywhere; font-size: 11px; }
.ledger-run-evidence { margin-top: 17px; padding-top: 14px; border-top: 1px solid var(--aq-border); }
.ledger-version { margin: -2px 0 8px; }
.ledger-receipt { display: block; margin: 0 0 5px; overflow-wrap: anywhere; }
.ledger-issues { margin-top: 12px; }
.ledger-issues h4 { margin: 0 0 6px; font-size: 14px; }
.ledger-issues ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.ledger-issues li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--aq-border); }
.ledger-select-run { display: flex; align-items: center; gap: 7px; margin-top: 12px; }
.compact-btn { min-height: 32px; padding-inline: 9px; font-size: 12px; }
.muted { color: var(--aq-muted); font-size: 13px; }
.status { display: flex; align-items: center; gap: 7px; margin: 10px 0 0; font-size: 13px; }
@media (max-width: 780px) { .ledger-summary-grid { grid-template-columns: repeat(3, minmax(80px, 1fr)); } }
@media (max-width: 480px) { .ledger-summary-grid { grid-template-columns: repeat(2, minmax(80px, 1fr)); } .ledger-detail-head { align-items: flex-start; } }
</style>
