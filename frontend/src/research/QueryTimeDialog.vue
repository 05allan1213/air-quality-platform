<script lang="ts">
export type QueryTimeResume = {
  window: import('./queryContract').ObservationWindow
  start: string
  end: string
  asOf: string
  reportDate: string
  focus: string
}
</script>

<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { ElDatePickerPanel, ElDialog, ElOption, ElSelect, ElTooltip } from 'element-plus'
import { normalizedInstant, validDate, windows, type ObservationWindow } from './queryContract'

type TimeField = 'start' | 'end' | 'asOf' | 'reportDate'

const props = defineProps<{
  mode: 'daily' | 'latest' | 'range'
  choices: readonly ObservationWindow[]
  window: ObservationWindow
  start: string
  end: string
  asOf: string
  reportDate: string
  opener: HTMLElement | null
  resumeDraft: QueryTimeResume | null
}>()
const emit = defineEmits<{
  use: [draft: { window: ObservationWindow; start: string; end: string; asOf: string; reportDate: string }]
}>()
const open = defineModel<boolean>({ required: true })
const draftWindow = ref<ObservationWindow>('hourly_label_unspecified')
const draft = ref<Record<TimeField, string>>({ start: '', end: '', asOf: '', reportDate: '' })
const first = ref<HTMLElement | null>(null)
const resumeFocus = ref('')
const focusIds = new Set(['query-time-window', 'query-time-start', 'query-time-end', 'query-time-asOf',
  'query-time-reportDate', 'query-time-tip', 'query-time-cancel', 'query-time-keep', ...
  ['start', 'end', 'asOf', 'reportDate'].map(field => `query-calendar-${field}`)])
const error = ref('')
const selectOpen = ref(false)
const activeCalendar = ref<TimeField | null>(null)
const calendarSelection = ref('')
const helpOpen = ref(false)
let focusReturnTarget: HTMLElement | null = null
let inertShell: HTMLElement | null = null

const fields = computed<{ key: TimeField; label: string }[]>(() => {
  if (props.mode === 'daily') return [{ key: 'reportDate', label: '源地方报告日' }]
  if (props.mode === 'latest') return [{ key: 'asOf', label: '截止时刻（可选）' }]
  return [{ key: 'start', label: '开始时刻（含）' }, { key: 'end', label: '结束时刻（不含）' }]
})

watch(open, visible => {
  if (visible) {
    const resumed = props.resumeDraft
    draftWindow.value = resumed && props.choices.includes(resumed.window) ? resumed.window : props.window
    const { start, end, asOf, reportDate } = resumed ?? props
    draft.value = { start, end, asOf, reportDate }
    resumeFocus.value = resumed?.focus ?? ''
    error.value = ''
    focusReturnTarget = props.opener
    const shell = document.querySelector<HTMLElement>('.application')
    if (shell && !shell.inert) {
      shell.inert = true
      inertShell = shell
    }
  } else {
    activeCalendar.value = null
    selectOpen.value = false
    helpOpen.value = false
  }
})

function releaseBackground(restoreFocus = false) {
  if (inertShell) inertShell.inert = false
  inertShell = null
  if (restoreFocus) focusReturnTarget?.focus()
  focusReturnTarget = null
}

function focusIntro() {
  const dialog = first.value?.closest('.aq-query-time-dialog')
  if (dialog !== document.activeElement && dialog?.contains(document.activeElement)) return
  const target = focusIds.has(resumeFocus.value) ? document.getElementById(resumeFocus.value) : null
  if (target && dialog?.contains(target)) target.focus()
  else first.value?.focus()
}

function capture(): QueryTimeResume | null {
  if (!open.value) return null
  const active = document.activeElement
  const field = activeCalendar.value
  const grid = field && document.getElementById(`query-calendar-grid-${field}`)
  const focus = grid && grid.contains(active) ? `query-calendar-${field}` : active?.id ?? ''
  return { window: draftWindow.value, ...draft.value, focus: focusIds.has(focus) ? focus : '' }
}

defineExpose({ capture })

function calendarDay(field: TimeField) {
  const value = draft.value[field].slice(0, 10)
  return validDate(value) ? value : ''
}

function closeCalendar(field: TimeField, restoreFocus: boolean) {
  if (activeCalendar.value !== field) return
  activeCalendar.value = null
  if (restoreFocus) void nextTick(() => document.getElementById(`query-calendar-${field}`)?.focus())
}

function toggleCalendar(field: TimeField) {
  if (activeCalendar.value === field) {
    closeCalendar(field, false)
    return
  }
  calendarSelection.value = calendarDay(field)
  activeCalendar.value = field
}

function selectCalendarDay(value: unknown) {
  if (typeof value === 'string' && validDate(value)) calendarSelection.value = value
}

function chooseDay(field: TimeField, event: MouseEvent | KeyboardEvent) {
  // The panel also emits model updates while the user navigates with arrow keys.
  if (!(event.target instanceof Element) || !event.target.closest('.el-date-table td')) return
  const value = calendarSelection.value
  if (!value) return
  if (field === 'reportDate') draft.value.reportDate = value
  else draft.value[field] = value + (calendarDay(field) ? draft.value[field].slice(10) || 'T00:00:00Z' : 'T00:00:00Z')
  closeCalendar(field, true)
}

function focusCalendar(field: TimeField) {
  document.querySelector<HTMLElement>(`#query-calendar-grid-${field} [role="grid"] [tabindex="0"]`)?.focus()
}

function onEscape(event: KeyboardEvent) {
  if (!open.value || event.key !== 'Escape' || event.isComposing || selectOpen.value) return
  event.preventDefault()
  event.stopPropagation()
  if (activeCalendar.value) closeCalendar(activeCalendar.value, true)
  else if (helpOpen.value) helpOpen.value = false
  else open.value = false
}
function onPointerDown(event: PointerEvent) {
  const field = activeCalendar.value
  if (field && event.target instanceof Element &&
      !event.target.closest(`#query-calendar-${field}, .aq-query-calendar-popup`)) closeCalendar(field, false)
}
window.addEventListener('keydown', onEscape, true)
window.addEventListener('pointerdown', onPointerDown, true)
onScopeDispose(() => {
  window.removeEventListener('keydown', onEscape, true)
  window.removeEventListener('pointerdown', onPointerDown, true)
  releaseBackground()
})

function useDraft() {
  const { start, end, asOf, reportDate } = draft.value
  const first = normalizedInstant(start)
  const last = normalizedInstant(end)
  if (props.mode === 'daily' && !validDate(reportDate)) error.value = '请输入有效的源地方报告日。'
  else if (props.mode === 'latest' && asOf.trim() && !normalizedInstant(asOf)) error.value = '截止时刻须带秒和时区偏移。'
  else if (props.mode === 'range' && (!first || !last || Date.parse(first) >= Date.parse(last))) error.value = '请输入带秒和时区偏移的时刻，结束须晚于开始。'
  else {
    emit('use', { window: draftWindow.value, ...draft.value })
    open.value = false
    return
  }
  void nextTick(() => document.getElementById('query-time-draft-error')?.focus())
}
</script>

<template>
  <ElDialog v-model="open" append-to-body class="aq-query-time-dialog" modal-class="aq-query-time-scrim"
    title="设置查询时段" width="min(30rem, calc(100% - 24px))" align-center destroy-on-close
    :show-close="false" :close-on-click-modal="false" :close-on-press-escape="false"
    @opened="focusIntro" @closed="releaseBackground(true)">
    <div class="query-time-fields">
      <p ref="first" tabindex="-1" class="query-time-hint">保留条件后，回到查询页应用。</p>
      <label v-if="mode !== 'daily'" class="field" for="query-time-window">统计窗口
        <ElSelect id="query-time-window" v-model="draftWindow" aria-label="统计窗口" :teleported="false"
          popper-class="aq-query-select-popup" @visible-change="selectOpen = $event">
          <ElOption v-for="window in choices" :key="window" :label="windows[window].label" :value="window" />
        </ElSelect>
      </label>
      <div v-for="field in fields" :key="field.key" class="query-time-field">
        <label class="field" :for="`query-time-${field.key}`">{{ field.label }}</label>
        <div class="query-time-input">
          <input :id="`query-time-${field.key}`" v-model="draft[field.key]" type="text" autocomplete="off"
            :placeholder="field.key === 'reportDate' ? '2024-02-01' : '2024-02-01T09:00:00+08:00'"
            aria-describedby="query-time-format">
          <ElTooltip :visible="activeCalendar === field.key" :teleported="false" placement="bottom-end" role="dialog"
            effect="light" :aria-label="`${field.label}日历`"
            :fallback-placements="['top-end', 'bottom-start', 'top-start']" :show-arrow="false" :persistent="false"
            :popper-style="{ width: 'min(17rem, calc(100vw - 36px))' }" popper-class="aq-query-calendar-popup"
            @show="focusCalendar(field.key)">
            <button :id="`query-calendar-${field.key}`" class="btn" type="button"
              :aria-label="`选日期：${field.label}`" @click="toggleCalendar(field.key)">选日期</button>
            <template #content>
              <div :id="`query-calendar-grid-${field.key}`" @click="chooseDay(field.key, $event)"
                @keydown.enter="chooseDay(field.key, $event)" @keydown.space="chooseDay(field.key, $event)">
                <ElDatePickerPanel :model-value="calendarSelection" type="date" value-format="YYYY-MM-DD"
                  @update:model-value="selectCalendarDay" />
              </div>
            </template>
          </ElTooltip>
        </div>
      </div>
      <div class="query-time-help">
        <p id="query-time-format" class="query-time-hint">{{ mode === 'daily' ? '使用来源发布的地方日期。' : '时刻须含秒和时区偏移。选日期会保留已有时分秒和偏移，空白时使用 UTC 零点。' }}</p>
        <ElTooltip v-if="mode !== 'daily'" v-model:visible="helpOpen" :trigger="['hover', 'focus', 'click']" :show-after="180"
          :teleported="false" popper-class="aq-query-time-tooltip" content="Z 表示 UTC。也可输入 +08:00 等明确偏移。">
          <button id="query-time-tip" class="btn query-time-tip" type="button">偏移说明</button>
        </ElTooltip>
      </div>
      <p v-if="error" id="query-time-draft-error" class="status status-error" role="alert" tabindex="-1">{{ error }}</p>
    </div>
    <template #footer>
      <div class="query-time-actions">
        <button id="query-time-cancel" class="btn" type="button" @click="open = false">取消</button>
        <button id="query-time-keep" class="btn primary" type="button" @click="useDraft">保留条件</button>
      </div>
    </template>
  </ElDialog>
</template>

<style>
.aq-query-time-scrim.el-overlay { --el-overlay-color-lighter: rgb(22 54 80 / 24%); }
.el-dialog.aq-query-time-dialog {
  --el-dialog-bg-color: var(--aq-content);
  --el-dialog-padding-primary: clamp(12px, 4vw, 22px);
  --el-dialog-border-radius: 18px;
  color: var(--aq-text);
  border: 1px solid var(--aq-control-border);
  box-shadow: var(--aq-float-shadow);
}
@supports (backdrop-filter: blur(1px)) {
  @media (prefers-reduced-transparency: no-preference) and (prefers-reduced-motion: no-preference) and (forced-colors: none) {
    :root:not(:has(.application[data-material='solid'])) .aq-query-time-dialog {
      --el-dialog-bg-color: rgb(242 250 255 / 92%);
      backdrop-filter: blur(14px);
    }
  }
}
.aq-query-time-dialog .el-dialog__title { color: var(--aq-text); font-weight: 650; }
.aq-query-time-dialog .el-dialog__body { color: var(--aq-text); }
.aq-query-time-dialog .query-time-fields { display: grid; gap: 12px; }
.aq-query-time-dialog :is(.query-time-hint, .status) { margin: 0; }
.aq-query-time-dialog .query-time-hint { color: var(--aq-secondary); font-size: .875rem; }
.aq-query-time-dialog .query-time-input { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; }
.aq-query-time-dialog .query-time-field > label { margin-bottom: 4px; }
.aq-query-time-dialog .query-time-input > input { min-width: 0; min-height: 44px; font-size: .875rem; padding: 7px 8px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: var(--aq-content); color: var(--aq-text); }
.aq-query-time-dialog .el-select__wrapper { min-height: 44px; background: var(--aq-content); }
.aq-query-time-dialog .el-select__wrapper.is-focused { box-shadow: 0 0 0 2px var(--aq-focus) inset; }
.aq-query-time-dialog .query-time-help, .aq-query-time-dialog .query-time-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.aq-query-time-dialog .query-time-actions { justify-content: flex-end; }
.aq-query-time-dialog .btn { min-height: 44px; white-space: nowrap; }
.aq-query-time-dialog .query-time-tip { min-height: 32px; padding: 3px 9px; font-size: .875rem; }
.aq-query-time-dialog .el-popper:is(.aq-query-select-popup, .aq-query-calendar-popup) {
  --el-bg-color-overlay: var(--aq-content);
  --el-fill-color-blank: var(--aq-content);
  border-color: var(--aq-control-border);
  box-shadow: var(--aq-float-shadow);
}
.aq-query-time-dialog .el-popper.aq-query-calendar-popup { padding: 2px; }
.aq-query-calendar-popup .el-date-picker { --el-datepicker-off-text-color: var(--aq-secondary); }
.aq-query-calendar-popup .el-date-picker, .aq-query-calendar-popup .el-picker-panel__content { width: auto; }
.aq-query-calendar-popup .el-picker-panel__content { margin: 6px; }
.aq-query-calendar-popup .el-date-picker__header { margin: 8px 4px; }
.aq-query-calendar-popup [role='grid'] [tabindex]:focus-visible { outline: 2px solid var(--aq-focus); outline-offset: -2px; }
.aq-query-time-dialog .el-popper.aq-query-time-tooltip { max-width: 15rem; background: #21465e; }
.aq-query-time-dialog .aq-query-time-tooltip .el-popper__arrow::before { background: #21465e; }
</style>
