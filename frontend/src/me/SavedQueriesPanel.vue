<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { EditPen, RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { ApiError, api, usePrivateRead } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import OwnedState from './OwnedState.vue'
import { timeLabel, useOwnedPage } from './owned'

type Selection = {
  product: number | null; source: string; indicators: string[]; cities?: (number | null)[]
  site_epochs?: (number | null)[]; start: string; end: string
  [key: string]: unknown
}
type Saved = {
  id: string; title: string; intent_revision: number; updated_at: string; selection: Selection
  method: 'statistics' | 'compare' | 'pearson'
}
type Coverage = { pos: number; entity: number; indicator: string; unit: string; E: number | null; O: number; V: number }
type RunResult = {
  state: 'current' | 'data_changed' | 'baseline_unavailable' | 'target_unavailable' | 'method_changed'
  result?: {
    state: 'ok' | 'no_data'; source_timezone: string; attribution?: string[]; selection: Selection
    columns: Coverage[]; E_common?: number | null; P?: number | null; pearson?: number | null
    whole_period_mean?: number | string | null; partial_mean?: number | string | null
    conclusion?: { state: string }
  }
}

const session = useSessionStore()
const runRead = usePrivateRead<RunResult>()
const runId = ref('')
const edit = ref<Saved>()
const removeId = ref('')
const draft = reactive({ title: '', start: '', end: '' })
const { data, busy, error, notice, actionError, actionBusy, cursor, list, item, act } = useOwnedPage<Saved>('saved-queries', resetForms)
const labels = { statistics: '趋势与分布', compare: '对象对比', pearson: '指标关联' }
const runLabels = {
  current: '与方案保存时的输入一致。', data_changed: '输入数据已变化，以下为本次重新运行的结果。',
  baseline_unavailable: '当前无法与已保存基线对照，以下为本次重新运行的结果。',
  target_unavailable: '所选对象当前不可用。', method_changed: '分析方法已更新，请在工作台重新选择条件。',
}
const conclusionLabels: Record<string, string> = {
  sufficient: '覆盖达到探索分析阈值', partial_coverage: '覆盖不完整', insufficient: '覆盖不足',
  insufficient_coverage: '覆盖不足', denominator_unknown: '应到分母未知',
  insufficient_pairs: '有效配对不足', zero_variance: '存在零方差',
  uncertain_coverage: '覆盖依据不完整', no_data: '没有可用数据',
}
const draftChanged = computed(() => edit.value && (
  draft.title.trim() !== edit.value.title || draft.start !== edit.value.selection.start || draft.end !== edit.value.selection.end
))

function resetForms() {
  edit.value = undefined
  removeId.value = ''
  runId.value = ''
  Object.assign(draft, { title: '', start: '', end: '' })
  runRead.clear()
}

function reload(next?: string) {
  resetForms()
  void list(next)
}

function available(saved: Saved) {
  const selected = saved.selection
  return selected.product !== null && (selected.cities ?? selected.site_epochs ?? []).every(id => id !== null)
}

function startEdit(saved: Saved) {
  resetForms()
  edit.value = saved
  Object.assign(draft, { title: saved.title, start: saved.selection.start, end: saved.selection.end })
}

function update() {
  const saved = edit.value
  if (!saved || !draftChanged.value) return
  const hasNewWindow = draft.start !== saved.selection.start || draft.end !== saved.selection.end
  const isoOffset = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/
  if (hasNewWindow && (!isoOffset.test(draft.start) || !isoOffset.test(draft.end)
    || !(Date.parse(draft.start) < Date.parse(draft.end)))) {
    actionError.value = '请输入带时区的开始、结束时间，且结束时间应晚于开始时间。'
    return
  }
  const changes = { intent_revision: saved.intent_revision, title: draft.title.trim(),
    ...(hasNewWindow ? { selection: { ...saved.selection, start: draft.start, end: draft.end } } : {}),
  }
  void act(() => api.patch<Saved>(item(saved.id), changes), () => {
    notice.value = '分析方案已更新。'
    reload()
  }, cause => {
    if (cause instanceof ApiError && (cause.status === 403 || cause.status === 404 || cause.status === 409)) reload()
  })
}

function remove(id: string) {
  void act(() => api.delete(item(id)), () => {
    notice.value = '分析方案已删除。'
    reload()
  }, cause => { if (cause instanceof ApiError && cause.status === 404) reload() })
}

function run(saved: Saved) {
  resetForms()
  runId.value = saved.id
  void runRead.run(signal => api.post<RunResult>(item(saved.id, 'run/'), undefined, signal))
}

function requestRemoval(saved: Saved) {
  resetForms()
  removeId.value = saved.id
}
</script>

<template>
  <section class="me-panel" aria-labelledby="my-saved-title">
    <header class="me-panel-head">
      <div><h2 id="my-saved-title">分析方案</h2><p>重新运行时会检查当前权限，并读取所选时间段的数据。</p></div>
      <button class="btn" type="button" :disabled="busy || actionBusy" @click="reload()"><RefreshRight aria-hidden="true" />刷新方案</button>
    </header>
    <p v-if="notice" class="status status-success" role="status">{{ notice }}</p>
    <p v-if="actionError" class="status status-error" role="alert">{{ actionError }}</p>
    <OwnedState :busy="busy" :error="error?.message" :empty="data?.results.length === 0" empty-text="还没有分析方案。" @retry="reload()" />
    <RouterLink v-if="data?.results.length === 0" class="btn me-fit" :to="{ name: 'analysis', query: scopeQuery(session.scope) }">打开分析工作台</RouterLink>
    <ul v-if="data?.results.length" class="me-list">
      <li v-for="saved in data.results" :key="saved.id" class="me-row">
        <article class="me-entry">
          <div class="me-entry-head">
            <div>
              <h3>{{ saved.title }}</h3>
              <p>{{ labels[saved.method] }} · 更新于 {{ timeLabel(saved.updated_at) }}</p>
              <p v-if="available(saved)">{{ saved.selection.indicators.join(' / ') }} · {{ timeLabel(saved.selection.start) }} 至 {{ timeLabel(saved.selection.end) }}（不含结束时刻）</p>
              <p v-else>所选对象当前不可用。</p>
            </div>
            <div class="me-actions">
              <button class="btn primary" type="button" :disabled="actionBusy || runRead.busy.value" @click="run(saved)"><VideoPlay aria-hidden="true" />重新运行</button>
              <button v-if="available(saved)" class="btn" type="button" :disabled="actionBusy" @click="startEdit(saved)"><EditPen aria-hidden="true" />编辑方案</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="requestRemoval(saved)">删除方案</button>
            </div>
          </div>
          <form v-if="edit?.id === saved.id" class="me-details me-edit" @submit.prevent="update">
            <label class="field">方案名称<input v-model="draft.title" maxlength="100" required /></label>
            <label class="field">开始时间（含）<input v-model.trim="draft.start" maxlength="35" required spellcheck="false" /></label>
            <label class="field">结束时间（不含）<input v-model.trim="draft.end" maxlength="35" required spellcheck="false" /></label>
            <p class="me-muted">时间需包含时区，例如 2024-02-01T00:00:00Z。</p>
            <div class="me-actions">
              <button class="btn primary" type="submit" :disabled="actionBusy || !draftChanged || !draft.title.trim()">保存方案</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="resetForms">取消修改</button>
            </div>
          </form>
          <div v-if="removeId === saved.id" class="me-details">
            <p>确定删除这份分析方案？</p>
            <div class="me-actions">
              <button class="btn" type="button" :disabled="actionBusy" @click="remove(saved.id)">确认删除方案</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="resetForms">取消</button>
            </div>
          </div>
          <div v-if="runId === saved.id" class="me-details me-entry">
            <h4>本次运行结果</h4>
            <OwnedState :busy="runRead.busy.value" :error="runRead.error.value?.message" :empty="false" empty-text="" @retry="run(saved)" />
            <template v-if="runRead.data.value">
              <p class="status" role="status">{{ runLabels[runRead.data.value.state] }}</p>
              <template v-if="runRead.data.value.result">
                <p v-if="runRead.data.value.result.state === 'no_data'">当前条件没有获准数据。</p>
                <p>来源时区 {{ runRead.data.value.result.source_timezone }}<template v-if="runRead.data.value.result.conclusion"> · {{ conclusionLabels[runRead.data.value.result.conclusion.state] ?? runRead.data.value.result.conclusion.state }}</template></p>
                <div class="me-readings" aria-label="本次有效覆盖">
                  <div v-for="column in runRead.data.value.result.columns" :key="column.pos" class="me-reading">
                    <b>{{ column.indicator }} · {{ column.unit }}</b>
                    <span>对象 {{ column.entity }} · 应到 {{ column.E ?? '未知' }} · 实到 {{ column.O }} · 有效 {{ column.V }}</span>
                  </div>
                </div>
                <p v-if="runRead.data.value.result.E_common !== undefined">共同应到 {{ runRead.data.value.result.E_common ?? '未知' }} · 有效配对 {{ runRead.data.value.result.P ?? '未知' }}</p>
                <p v-if="runRead.data.value.result.pearson != null">Pearson 相关系数 {{ runRead.data.value.result.pearson }}。相关性不代表因果关系。</p>
                <p v-if="runRead.data.value.result.whole_period_mean != null">全期均值 {{ runRead.data.value.result.whole_period_mean }} {{ runRead.data.value.result.columns[0]?.unit }}</p>
                <p v-if="runRead.data.value.result.partial_mean != null">部分样本均值 {{ runRead.data.value.result.partial_mean }} {{ runRead.data.value.result.columns[0]?.unit }}</p>
                <p v-if="runRead.data.value.result.attribution?.length" class="me-muted">{{ runRead.data.value.result.attribution.join(' · ') }}</p>
              </template>
            </template>
          </div>
        </article>
      </li>
    </ul>
    <nav v-if="cursor || data?.page.next_cursor" class="me-actions me-pagination" aria-label="方案分页">
      <button v-if="cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload()">返回第一页</button>
      <button v-if="data?.page.next_cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload(data.page.next_cursor)">下一页</button>
    </nav>
  </section>
</template>
