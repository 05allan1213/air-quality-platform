<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { Calendar, CircleCheck, CircleClose, Refresh, View } from '@element-plus/icons-vue'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import CollectionLedger from './CollectionLedger.vue'
import { beginSchedule, pendingSchedule, scheduleReceipt, type RecordedSchedule } from './scheduleReceipt'
import {
  cadenceLabel,
  createScheduleDraftCommand,
  decideScheduleCommand,
  eligibleFirstDueAt,
  proposeScheduleCommand,
  type ScheduleCommand,
  type SchedulePlan,
  type ScheduleTemplateOption,
  type ScheduleTemplatePage,
} from './scheduleContract'

const session = useSessionStore()
const directoryRead = usePrivateRead<ScheduleTemplatePage>()
const planRead = usePrivateRead<SchedulePlan>()
const successorRead = usePrivateRead<SchedulePlan>()
const templates = ref<ScheduleTemplateOption[]>([])
const selectedTemplateId = ref<number | null>(null)
const nextCursor = ref<string | null>(null)
const cadenceSeconds = ref(3600)
const firstDueAt = ref('')
const errorMessage = ref('')
const notice = ref('')
const outcomeUnknown = computed(() => pendingSchedule.value !== null)
const actionPending = ref(false)
const confirmAction = ref<'activate' | 'discard' | null>(null)
const receipt = ref<RecordedSchedule | null>(null)
let writeController: AbortController | null = null

const canConfigure = computed(() => session.capabilities.actions.includes('configure_collection_schedule'))
const directoryBusy = computed(() => directoryRead.busy.value)
const planBusy = computed(() => planRead.busy.value)
const planError = computed(() => planRead.error.value)
const selectedTemplate = computed(() => templates.value.find(item => item.template === selectedTemplateId.value) ?? null)
const currentPlan = computed(() => planRead.data.value)
const pendingPlan = computed(() => successorRead.data.value)
const controllablePlan = computed(() => pendingPlan.value ?? (currentPlan.value?.state === 'draft' ? currentPlan.value : null))
const firstDueValid = computed(() => selectedTemplate.value
  ? eligibleFirstDueAt(firstDueAt.value, selectedTemplate.value.delivery_minute)
  : false)

function uuid() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function nextDue(minute: number) {
  const date = new Date(Date.now() + 2 * 60 * 60 * 1000)
  date.setUTCMinutes(minute, 0, 0)
  if (date.getTime() <= Date.now()) date.setUTCHours(date.getUTCHours() + 1)
  return date.toISOString().replace('.000Z', 'Z')
}

function resetSelectedState() {
  planRead.clear()
  successorRead.clear()
  confirmAction.value = null
  receipt.value = null
  errorMessage.value = ''
  notice.value = ''
}

function clearWorkspace() {
  directoryRead.clear()
  resetSelectedState()
  templates.value = []
  selectedTemplateId.value = null
  nextCursor.value = null
  firstDueAt.value = ''
}

async function readPlan(jobId: number) {
  successorRead.clear()
  const result = await planRead.run(signal => api.get<SchedulePlan>(`admin/collection-jobs/${jobId}/schedule/`, undefined, signal))
  if (!result) {
    if ([403, 404].includes(planRead.error.value?.status ?? 0)) {
      clearWorkspace()
      errorMessage.value = '计划已不可访问，请重新读取当前授权目录。'
    }
    return
  }
  if (selectedTemplate.value?.job !== jobId) return
  if (result.pending_successor !== null) {
    const successor = await successorRead.run(signal => api.get<SchedulePlan>(`admin/collection-jobs/${result.pending_successor}/schedule/`, undefined, signal))
    if (!successor && [403, 404].includes(successorRead.error.value?.status ?? 0)) {
      clearWorkspace()
      errorMessage.value = '后继计划已不可访问，请重新读取当前授权目录。'
    }
  }
}

async function selectTemplate(option: ScheduleTemplateOption) {
  selectedTemplateId.value = option.template
  resetSelectedState()
  cadenceSeconds.value = option.allowed_cadences[0] ?? 3600
  firstDueAt.value = nextDue(option.delivery_minute)
  if (option.job !== null) await readPlan(option.job)
}

async function loadDirectory(cursor: string | null = null) {
  if (!canConfigure.value) return
  errorMessage.value = ''
  confirmAction.value = null
  const result = await directoryRead.run(signal => api.get<ScheduleTemplatePage>('admin/catalog/products/', {
    purpose: 'configure',
    scope: session.scope || undefined,
    page_size: 25,
    cursor: cursor ?? undefined,
  }, signal))
  if (!result) {
    const cause = directoryRead.error.value
    if (!cause) return
    const message = cause?.status === 403
      ? '调度配置权限已撤销或目录当前不可用。'
      : cause?.code === 'page_changed'
        ? '调度目录已变化，请重新读取。'
        : cause?.message ?? '读取调度目录失败。'
    if (cause?.status === 403 || cause?.status === 409) {
      if (cause.status === 403) pendingSchedule.value = null
      clearWorkspace()
      errorMessage.value = message
    } else {
      errorMessage.value = message
    }
    return
  }
  templates.value = cursor ? [...templates.value, ...result.results] : result.results
  nextCursor.value = result.next_cursor
  if (selectedTemplateId.value !== null) {
    const refreshed = templates.value.find(item => item.template === selectedTemplateId.value)
    if (refreshed) {
      cadenceSeconds.value = refreshed.allowed_cadences.includes(cadenceSeconds.value)
        ? cadenceSeconds.value
        : refreshed.allowed_cadences[0] ?? 3600
      if (refreshed.job !== null) await readPlan(refreshed.job)
      else resetSelectedState()
    } else {
      selectedTemplateId.value = null
      resetSelectedState()
    }
  }
}

function errorText(cause: unknown) {
  if (!(cause instanceof ApiError)) return '操作未确认，请重新读取计划后核对状态。'
  if (cause.status === 409 || cause.code === 'precondition_conflict') return '计划、模板或窗口已变化；已重新读取当前状态，请确认后再提交新的操作。'
  if (cause.status === 403 && cause.code !== 'csrf_failed') return '当前会话不再具备此配置权限，计划操作已停止。'
  return cause.message
}

async function runCommand(original: NonNullable<typeof pendingSchedule.value>, lookup: boolean) {
  if (actionPending.value || !canConfigure.value || pendingSchedule.value !== original) return
  actionPending.value = true
  errorMessage.value = ''
  notice.value = ''
  confirmAction.value = null
  const active = new AbortController()
  const generation = privateStateGeneration()
  writeController = active
  const current = () => !active.signal.aborted && privateStateGeneration() === generation && pendingSchedule.value === original
  try {
    const path = original.command.action === 'create_draft' ? 'admin/collection-jobs/' : `admin/collection-jobs/${original.target}/schedule/`
    const result = lookup
      ? await api.post<unknown>('admin/command-receipts/lookup/', original, active.signal)
      : await api.post<unknown>(path, original.command, active.signal)
    if (!current()) return
    const confirmed = scheduleReceipt(result, original, lookup)
    pendingSchedule.value = null
    await loadDirectory()
    if (active.signal.aborted || generation !== privateStateGeneration() || !canConfigure.value || !templates.value.length || errorMessage.value) return
    receipt.value = confirmed
    notice.value = `已确认原操作：Job ${confirmed.job}，${stateName(confirmed.state)}。`
  } catch (cause) {
    if (!current()) return
    if (lookup) {
      errorMessage.value = cause instanceof ApiError && cause.status === 404 ? '未取得可披露的本人回执，原操作仍待核对。' : '未能核对原回执，请稍后再查。'
      return
    }
    if (!(cause instanceof ApiError) || cause.code === 'request_failed' || ![400, 403, 404, 409, 429].includes(cause.status)) {
      errorMessage.value = '未收到可核验的计划回执。'
      return
    }
    pendingSchedule.value = null
    errorMessage.value = errorText(cause)
    if (cause.status === 403 && cause.code !== 'csrf_failed') {
      clearWorkspace()
      errorMessage.value = '当前会话不再具备此配置权限，计划操作已停止。'
    } else if (cause.status === 409) {
      const job = selectedTemplate.value?.job
      if (job !== null && job !== undefined) await readPlan(job)
      else await loadDirectory()
      if (!active.signal.aborted && generation === privateStateGeneration() && !planRead.error.value && !directoryRead.error.value && templates.value.length) errorMessage.value = errorText(cause)
    }
  } finally {
    if (writeController === active) { writeController = null; actionPending.value = false }
  }
}

function submit(command: ScheduleCommand, jobId: number | null) {
  if (!canConfigure.value || actionPending.value) return
  const original = beginSchedule(command, jobId)
  if (original) void runCommand(original, false)
}

function createDraft() {
  const option = selectedTemplate.value
  if (!option) return
  const command = createScheduleDraftCommand(option, cadenceSeconds.value, firstDueAt.value, uuid())
  if (!command) {
    errorMessage.value = '请选择模板允许的频率，并输入匹配投递分钟、带时区且秒为零的未来时刻。'
    return
  }
  void submit(command, null)
}

function proposeSuccessor() {
  const plan = currentPlan.value
  const option = selectedTemplate.value
  if (!plan || !option) return
  const command = proposeScheduleCommand(plan, option, cadenceSeconds.value, firstDueAt.value, uuid())
  if (!command) {
    errorMessage.value = '当前计划不能提议后继，或新时刻与模板配置不匹配。'
    return
  }
  void submit(command, plan.job)
}

function confirmDecision(action: 'activate' | 'discard') {
  const plan = controllablePlan.value
  if (!plan) return
  const command = decideScheduleCommand(action, plan, uuid())
  if (!command) {
    errorMessage.value = '计划状态或版本不完整，请重新读取后再操作。'
    confirmAction.value = null
    return
  }
  void submit(command, plan.job)
}

function formatInstant(value: string | null) {
  return value ? value.replace('T', ' ').replace('+00:00', ' UTC').replace('Z', ' UTC') : '—'
}

function stateName(value: string) {
  return ({ unconfigured: '未配置', draft: '草稿', active: '生效中', discarded: '已放弃' } as Record<string, string>)[value] ?? value
}

function scheduleSummary(option: ScheduleTemplateOption) {
  return option.plan_state === 'unconfigured' ? '未配置' : `${stateName(option.plan_state)} · Job ${option.job}`
}

function clearPrivateData() {
  writeController?.abort()
  writeController = null
  actionPending.value = false
  clearWorkspace()
}

window.addEventListener('air-quality:clear-reads', clearPrivateData)
onScopeDispose(() => {
  window.removeEventListener('air-quality:clear-reads', clearPrivateData)
  writeController?.abort()
  clearWorkspace()
})

if (canConfigure.value) void loadDirectory()
</script>

<template>
  <div class="page-content schedule-page">
    <header class="section-head">
      <div>
        <h1>采集任务</h1>
        <p>周期配置 · {{ session.scope ? 'F 合成域' : '正式域' }}</p>
      </div>
      <button v-if="canConfigure" class="btn schedule-refresh" type="button" :disabled="directoryBusy || actionPending" @click="loadDirectory()">
        <el-icon><Refresh /></el-icon><span>重新读取</span>
      </button>
    </header>

    <CollectionLedger />

    <section class="schedule-directory" aria-labelledby="schedule-directory-title">
      <div class="schedule-section-head">
        <h2 id="schedule-directory-title">周期配置</h2>
        <span v-if="canConfigure && templates.length" class="schedule-count">{{ templates.length }} 项当前获权模板</span>
      </div>

      <p v-if="!canConfigure" class="status schedule-permission" role="status">当前账号没有周期配置权限。</p>
      <p v-else-if="directoryBusy && templates.length === 0" class="session-state" role="status">正在读取当前会话获准的模板…</p>
      <p v-else-if="!errorMessage && templates.length === 0" class="status schedule-empty">当前授权范围内没有可配置的采集模板。</p>

      <template v-if="canConfigure && templates.length">
        <div class="table-scroll schedule-table-scroll">
          <table class="schedule-table">
            <caption class="sr-only">获准配置的采集计划模板</caption>
            <thead>
              <tr><th scope="col">产品 / 来源</th><th scope="col">授权对象</th><th scope="col">频率</th><th scope="col">计划状态</th><th scope="col">版本</th><th scope="col">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in templates" :key="item.template" :class="{ 'schedule-row-selected': item.template === selectedTemplateId }">
                <td><strong>{{ item.product }}</strong><span class="schedule-secondary">{{ item.source }} · {{ item.contract_version }}</span></td>
                <td>{{ item.scope_label }}</td>
                <td>{{ item.allowed_cadences.map(cadenceLabel).join('、') }}</td>
                <td><span class="schedule-state" :class="`schedule-state-${item.plan_state}`">{{ scheduleSummary(item) }}</span></td>
                <td>模板 {{ item.template_version }}<span v-if="item.job !== null" class="schedule-secondary">Job {{ item.job }}</span></td>
                <td><button class="btn schedule-open" type="button" :disabled="actionPending" :aria-pressed="item.template === selectedTemplateId" @click="selectTemplate(item)"><el-icon><View /></el-icon><span>打开计划</span></button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="schedule-pagination">
          <span v-if="directoryBusy" role="status">正在读取…</span>
          <button v-if="nextCursor" class="btn" type="button" :disabled="directoryBusy" @click="loadDirectory(nextCursor)">读取下一页</button>
        </div>
      </template>

      <p v-if="canConfigure && errorMessage" class="status status-error" role="alert">{{ errorMessage }}</p>
      <div v-if="canConfigure && pendingSchedule && !actionPending" class="schedule-actions" role="group" aria-label="待核对的计划操作">
        <p>计划操作结果待核对。刷新目录不会确认这次提交。</p>
        <button class="btn" type="button" @click="runCommand(pendingSchedule, true)">查本人计划回执</button>
      </div>
      <p v-if="notice" class="status status-success" role="status">{{ notice }}</p>
    </section>

    <section v-if="canConfigure && selectedTemplate" class="schedule-detail" aria-labelledby="schedule-detail-title">
      <div class="schedule-section-head">
        <div>
          <h2 id="schedule-detail-title">{{ selectedTemplate.scope_label }} · 产品 {{ selectedTemplate.product }}</h2>
          <p>{{ selectedTemplate.source }} · {{ selectedTemplate.contract_version }} · 投递分钟 {{ String(selectedTemplate.delivery_minute).padStart(2, '0') }} UTC</p>
        </div>
        <span class="schedule-state" :class="`schedule-state-${selectedTemplate.plan_state}`">{{ stateName(selectedTemplate.plan_state) }}</span>
      </div>

      <p v-if="planBusy" class="session-state" role="status">正在读取计划版本与切换预览…</p>
      <p v-else-if="selectedTemplate.job !== null && !currentPlan && planError" class="status status-error" role="alert">{{ planError.message }}</p>

      <template v-else-if="currentPlan">
        <dl class="schedule-facts">
          <div><dt>计划状态</dt><dd>{{ stateName(currentPlan.state) }}</dd></div>
          <div><dt>Job / 控制版本</dt><dd>{{ currentPlan.job }} / {{ currentPlan.version }}</dd></div>
          <div><dt>模板版本</dt><dd>{{ currentPlan.template_version }} / {{ currentPlan.current_template_version }}</dd></div>
          <div><dt>频率</dt><dd>{{ cadenceLabel(currentPlan.cadence_seconds) }}</dd></div>
          <div><dt>首次到期</dt><dd>{{ formatInstant(currentPlan.first_due_at) }}</dd></div>
          <div><dt>生效区间</dt><dd>[{{ formatInstant(currentPlan.effective_from) }}, {{ formatInstant(currentPlan.effective_until) }})</dd></div>
        </dl>
        <dl class="schedule-preview">
          <div><dt>切换边界</dt><dd>{{ formatInstant(currentPlan.preview.boundary) }}</dd></div>
          <div><dt>前驱最后时槽</dt><dd>{{ formatInstant(currentPlan.preview.previous_last_due_at) }}</dd></div>
          <div><dt>首个新时槽</dt><dd>{{ formatInstant(currentPlan.preview.first_due_at) }}</dd></div>
          <div><dt>间隔</dt><dd>{{ currentPlan.preview.gap_seconds === null ? '无前驱时槽' : `${currentPlan.preview.gap_seconds / 3600} 小时` }}</dd></div>
        </dl>

        <p v-if="currentPlan.pending_successor !== null" class="status status-warning schedule-warning">
          已登记后继草稿 Job {{ currentPlan.pending_successor }}。当前计划到切换边界前保持不变。
        </p>
        <p v-if="currentPlan.template_version !== currentPlan.current_template_version" class="status status-warning schedule-warning">
          草稿引用模板旧版本，不能按当前模板激活；请放弃后重新提议。
        </p>

        <p v-if="currentPlan.pending_successor !== null && successorRead.busy.value" class="session-state" role="status">正在读取后继草稿…</p>
        <p v-else-if="currentPlan.pending_successor !== null && !pendingPlan && successorRead.error.value" class="status status-error" role="alert">后继草稿不可读；为避免使用陈旧版本，已停用本区操作。</p>
        <template v-else-if="controllablePlan">
          <div class="schedule-subplan">
            <h3 v-if="pendingPlan">后继草稿 Job {{ pendingPlan.job }}</h3>
            <p class="schedule-footnote">草稿尚未启用，不会投递采集任务。</p>
            <p v-if="controllablePlan.preview.conflict === 'registered_window'" class="status status-warning schedule-warning">切换边界之后已有登记窗口，需重新选择首个到期时刻。</p>
            <dl class="schedule-preview">
              <div><dt>草稿版本</dt><dd>{{ controllablePlan.version }}</dd></div>
              <div><dt>拟生效边界</dt><dd>{{ formatInstant(controllablePlan.preview.boundary) }}</dd></div>
              <div><dt>首个到期</dt><dd>{{ formatInstant(controllablePlan.first_due_at) }}</dd></div>
              <div><dt>前驱版本</dt><dd>{{ controllablePlan.predecessor_version ?? '无' }}</dd></div>
            </dl>
            <div class="schedule-actions">
              <button class="btn primary" type="button" :disabled="actionPending || outcomeUnknown || currentPlan.template_version !== currentPlan.current_template_version || controllablePlan.preview.conflict !== null" @click="confirmAction = 'activate'"><el-icon><CircleCheck /></el-icon><span>激活计划</span></button>
              <button class="btn schedule-danger" type="button" :disabled="actionPending || outcomeUnknown" @click="confirmAction = 'discard'"><el-icon><CircleClose /></el-icon><span>放弃草稿</span></button>
            </div>
          </div>
        </template>
        <template v-else-if="currentPlan.state === 'active' && currentPlan.pending_successor === null">
          <form class="schedule-editor" @submit.prevent="proposeSuccessor">
            <h3>提议后继计划</h3>
            <div class="schedule-form-grid">
              <label>运行频率
                <select v-model.number="cadenceSeconds" required>
                  <option v-for="cadence in selectedTemplate.allowed_cadences" :key="cadence" :value="cadence">{{ cadenceLabel(cadence) }}</option>
                </select>
              </label>
              <label>首次到期时刻（含时区）
                <input v-model="firstDueAt" aria-label="首次到期时刻（含时区）" autocomplete="off" placeholder="2027-01-01T00:15:00Z" required />
              </label>
              <button class="btn primary" type="submit" :disabled="actionPending || outcomeUnknown || !firstDueValid"><el-icon><Calendar /></el-icon><span>提交后继草稿</span></button>
            </div>
            <p class="schedule-footnote">按 UTC 分钟槽提交偏移明确的时刻，秒须为 00。此操作只建立停用草稿。</p>
          </form>
        </template>
        <p v-else-if="currentPlan.state === 'active'" class="status schedule-empty">当前计划已有待处理变更。</p>
      </template>

      <form v-else-if="selectedTemplate.job === null && !planBusy" class="schedule-editor" @submit.prevent="createDraft">
        <h3>新建停用草稿</h3>
        <div class="schedule-form-grid">
          <label>运行频率
            <select v-model.number="cadenceSeconds" required>
              <option v-for="cadence in selectedTemplate.allowed_cadences" :key="cadence" :value="cadence">{{ cadenceLabel(cadence) }}</option>
            </select>
          </label>
          <label>首次到期时刻（含时区）
            <input v-model="firstDueAt" aria-label="首次到期时刻（含时区）" autocomplete="off" placeholder="2027-01-01T00:15:00Z" required />
          </label>
          <button class="btn primary" type="submit" :disabled="actionPending || outcomeUnknown || !firstDueValid"><el-icon><Calendar /></el-icon><span>建立停用草稿</span></button>
        </div>
        <p class="schedule-footnote">按 UTC 分钟槽提交偏移明确的时刻，秒须为 00。此操作不会开始采集。</p>
      </form>

      <div v-if="confirmAction" class="schedule-confirmation" role="group" aria-label="确认计划操作">
        <p>{{ confirmAction === 'activate' ? '确认在预览边界激活此计划？' : '确认放弃此草稿？活动计划不会因此停止。' }}</p>
        <button class="btn primary" type="button" :disabled="actionPending || outcomeUnknown" @click="confirmDecision(confirmAction)">{{ confirmAction === 'activate' ? '确认激活' : '确认放弃' }}</button>
        <button class="btn" type="button" :disabled="actionPending" @click="confirmAction = null">取消</button>
      </div>
      <p v-if="receipt" class="schedule-receipt">操作 {{ receipt.action_id }} · 计划版本 {{ receipt.version }}</p>
    </section>
  </div>
</template>

<style scoped>
.schedule-page { gap: 16px; }
.schedule-section-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
.schedule-section-head h2 { margin-bottom: 0; }
.schedule-section-head p, .section-head p { margin: 5px 0 0; color: var(--aq-secondary); }
.schedule-count { color: var(--aq-muted); font-size: 13px; }
.schedule-directory { min-width: 0; }
.schedule-table-scroll { max-width: 100%; overflow-x: auto; border: 1px solid var(--aq-border); border-radius: 8px; background: var(--aq-content-strong); }
.schedule-table { width: 100%; min-width: 860px; border-collapse: collapse; text-align: left; }
.schedule-table th, .schedule-table td { padding: 10px 12px; border-bottom: 1px solid var(--aq-border); vertical-align: middle; }
.schedule-table th { background: #e7f2f9; color: #35566e; font-size: 13px; font-weight: 650; }
.schedule-table tr:last-child td { border-bottom: 0; }
.schedule-table tr.schedule-row-selected td { background: #eaf5fb; }
.schedule-table td:first-child { min-width: 180px; }
.schedule-secondary { display: block; color: var(--aq-muted); font-size: 12px; overflow-wrap: anywhere; }
.schedule-state { display: inline-flex; max-width: 100%; min-height: 25px; align-items: center; padding: 2px 8px; border: 1px solid var(--aq-border); border-radius: 999px; background: #edf5fa; color: #395b73; font-size: 12px; line-height: 1.25; overflow-wrap: anywhere; }
.schedule-state-active { border-color: #9fcdbd; background: #e4f3ea; color: #126a50; }
.schedule-state-draft { border-color: #ddc89d; background: #f6efda; color: #825118; }
.schedule-open { min-height: 36px; padding-inline: 10px; }
.schedule-pagination { display: flex; min-height: 48px; align-items: center; justify-content: flex-end; gap: 12px; color: var(--aq-muted); }
.schedule-detail { min-width: 0; padding-top: 18px; border-top: 1px solid var(--aq-border); }
.schedule-facts, .schedule-preview { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 20px; margin: 16px 0; }
.schedule-facts > div, .schedule-preview > div { min-width: 0; padding: 9px 0; border-bottom: 1px solid var(--aq-border); }
.schedule-facts dt, .schedule-preview dt { color: var(--aq-muted); font-size: 12px; }
.schedule-facts dd, .schedule-preview dd { margin: 2px 0 0; overflow-wrap: anywhere; color: var(--aq-text); font-variant-numeric: tabular-nums; }
.schedule-subplan, .schedule-editor { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--aq-border); }
.schedule-subplan h3, .schedule-editor h3 { margin: 0 0 12px; font-size: 16px; }
.schedule-form-grid { display: grid; grid-template-columns: minmax(170px, .7fr) minmax(260px, 1.4fr) auto; align-items: end; gap: 12px; }
.schedule-form-grid label { display: grid; min-width: 0; gap: 5px; color: var(--aq-secondary); font-size: 13px; font-weight: 620; }
.schedule-form-grid input, .schedule-form-grid select { width: 100%; min-width: 0; min-height: 40px; padding: 7px 10px; border: 1px solid var(--aq-control-border); border-radius: 8px; background: #f8fcff; color: var(--aq-text); }
.schedule-form-grid input { font-variant-numeric: tabular-nums; }
.schedule-form-grid > .btn { min-height: 40px; }
.schedule-footnote { margin: 8px 0 0; color: var(--aq-muted); font-size: 12px; }
.schedule-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.schedule-danger { border-color: #d7b5b6; color: var(--aq-danger); }
.schedule-danger:hover { background: var(--aq-danger-bg); color: #762d31; }
.schedule-warning { margin: 12px 0; }
.schedule-confirmation { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 14px 0; padding: 12px 0; border-top: 1px solid var(--aq-border); border-bottom: 1px solid var(--aq-border); }
.schedule-confirmation p { width: 100%; margin: 0; color: var(--aq-text); font-weight: 620; }
.schedule-receipt { margin: 8px 0; color: var(--aq-muted); font-size: 12px; overflow-wrap: anywhere; }
.schedule-empty, .schedule-permission { margin: 10px 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }

@media (max-width: 760px) {
  .schedule-form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .schedule-form-grid > .btn { grid-column: 1 / -1; justify-self: start; }
  .schedule-facts, .schedule-preview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 480px) {
  .schedule-section-head { align-items: start; flex-direction: column; gap: 6px; }
  .schedule-form-grid, .schedule-facts, .schedule-preview { grid-template-columns: minmax(0, 1fr); }
  .schedule-form-grid > .btn { grid-column: auto; width: 100%; }
  .schedule-confirmation > .btn { flex: 1 1 130px; }
}
</style>
