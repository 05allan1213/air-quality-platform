<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef } from 'vue'
import { ArrowRight, CircleCheck, Document, Lock, Warning } from '@element-plus/icons-vue'
import { ElDialog } from 'element-plus'
import { usePreviewStore } from '../shared/preview'
import {
  actions,
  createExample,
  errorText,
  isActionAvailable,
  operatorId,
  performAction,
  statusText,
  type QualityAction,
  type QualityCommand,
  type QualityReason,
  type Scenario,
} from './qualityMock'

type Case = Scenario | 'readonly' | 'stale'

const preview = usePreviewStore()
const scenario = ref<Case>('unordered')
const example = shallowRef(createExample('unordered'))
const issue = computed(() => example.value.issue)
const canDecide = computed(() => preview.canGovern && scenario.value !== 'readonly')
const title = computed(() => issue.value.code === 'unordered_conflict' ? '无序异值争议' : '同版本异值争议')
const revisions = computed(() => example.value.observation.revisions)
const currentRevision = computed(() => revisions.value.find((revision) => revision.revision_id === issue.value.current_revision))
const candidateRevision = computed(() => revisions.value.find((revision) => revision.candidate))
const canCorrect = computed(() => isActionAvailable(example.value, 'correct_source'))
const canClaim = computed(() => isActionAvailable(example.value, 'claim'))
const canHold = computed(() => isActionAvailable(example.value, 'hold'))
const canClose = computed(() => isActionAvailable(example.value, 'close'))
const primaryAction = computed(() => canCorrect.value ? 'correct_source' : canClaim.value ? 'claim' : 'hold')
const lastAction = computed(() => example.value.localActions.at(-1))
const statusTone = computed(() => issue.value.status === 'resolved' ? 'green' : issue.value.status === 'in_review' ? 'blue' : '')
const actionNotice = computed(() => {
  if (issue.value.status === 'resolved') {
    return issue.value.disputed ? '工单已关闭。争议观测仍隔离。' : '审核已完成。旧资格依赖的结果需重新查询。'
  }
  if (!canDecide.value) return '当前账号仅可审阅，提交需质量裁决权限。'
  if (canCorrect.value) return '可信新版覆盖当前候选，允许按来源证据解除隔离。'
  return '候选缺少可信版本顺序，无法解除隔离。抓取时间不能作为裁决依据。'
})

const dialogOpen = ref(false)
const activeAction = ref<QualityAction>('claim')
const reason = ref<QualityReason | ''>('')
const pending = ref(false)
const feedback = ref('')
const error = ref('')
const actionHeading = ref<HTMLElement>()
const reasonField = ref<HTMLSelectElement>()
const dialogError = ref<HTMLElement>()
let actionTrigger: HTMLElement | null = null
let command: QualityCommand | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let simulateVersionChange = false
let focusReceipt = false

const impact: Record<QualityAction, string> = {
  claim: '将事件分配给当前演示账号，并进入审阅中。观测保持隔离。',
  hold: '事件留在审阅中，保留当前及候选修订证据。观测保持隔离。',
  close: '结束本事件的审阅。争议标记和数据隔离会继续保留。',
  correct_source: '按已核验的可信来源版本解除隔离。当前值和水位保持，依赖旧资格的结果需重新查询。',
}

const previewStatus = computed(() => activeAction.value === 'close' || activeAction.value === 'correct_source' ? 'resolved' : 'in_review')
const selectedAction = computed(() => actions[activeAction.value])

function timeLabel(stamp: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Shanghai',
  }).format(new Date(stamp))
}

function switchCase() {
  const selected = scenario.value
  example.value = createExample(selected === 'readonly' || selected === 'stale' ? 'unordered' : selected)
  simulateVersionChange = selected === 'stale'
  feedback.value = ''
}

function openAction(action: QualityAction, event: MouseEvent) {
  if (!canDecide.value || !isActionAvailable(example.value, action)) return
  actionTrigger = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  focusReceipt = false
  activeAction.value = action
  reason.value = ''
  error.value = ''
  command = {
    action,
    reason_code: '',
    request_id: crypto.randomUUID(),
    expected_version: issue.value.event_version,
    evidence_ref: action === 'correct_source' ? issue.value.evidence.correct_source ?? null : null,
  }
  dialogOpen.value = true
}

function restoreFocus() {
  if (focusReceipt || !actionTrigger?.isConnected || actionTrigger.matches(':disabled')) actionHeading.value?.focus()
  else actionTrigger.focus()
  actionTrigger = null
}

function submit() {
  if (!command || pending.value) return
  if (!reason.value) {
    error.value = '请选择处置理由。'
    void nextTick(() => reasonField.value?.focus())
    return
  }
  pending.value = true
  error.value = ''
  const submitted = { ...command, reason_code: reason.value }
  timer = setTimeout(() => {
    const before = example.value
    if (simulateVersionChange) {
      simulateVersionChange = false
      example.value = {
        ...before,
        trustedReceipt: 6490,
        issue: { ...before.issue, event_version: before.issue.event_version + 1, watermark: before.issue.watermark + 1 },
      }
    }
    const result = performAction(
      example.value,
      submitted,
      canDecide.value,
      '2024-05-16T07:05:00Z',
      crypto.randomUUID(),
    )
    pending.value = false
    if ('code' in result) {
      error.value = errorText[result.code]
      void nextTick(() => dialogError.value?.focus())
      return
    }
    example.value = result.example
    feedback.value = `${actions[result.response.outcome].label}已完成，事件为${statusText[result.response.status]}。`
    focusReceipt = true
    dialogOpen.value = false
  }, 180)
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="quality-page">
    <header class="page-title quality-title">
      <div class="title-copy">
        <h1>{{ title }}</h1>
        <p><span class="mono">事件 #{{ issue.id }}</span><span class="title-separator" aria-hidden="true" />云汀市 / 江湾站</p>
      </div>
      <div class="actions">
        <label class="sample-select" for="quality-scenario">演示情境</label>
        <select id="quality-scenario" v-model="scenario" data-testid="quality-scenario" @change="switchCase">
          <option value="unordered">无序异值 · 待处理</option>
          <option value="verified">同版本异值 · 证据齐备</option>
          <option value="resolved">同版本异值 · 已处置</option>
          <option value="readonly">仅有审阅权限</option>
          <option value="stale">提交时版本变化</option>
        </select>
      </div>
    </header>

    <section class="quality-context panel" aria-label="事件范围">
      <div class="context-status">
        <span class="tag amber"><Warning aria-hidden="true" />阻断级</span>
        <span class="tag" :class="statusTone" data-testid="quality-status">{{ statusText[issue.status] }}</span>
        <span class="muted context-code mono">{{ issue.code }}</span>
      </div>
      <dl class="context-grid">
        <div><dt>受影响产品</dt><dd>站点观测 <span class="context-reference">F · S 形态</span></dd></div>
        <div><dt>指标</dt><dd>{{ issue.affected.indicator }}<span class="context-reference">小时浓度</span></dd></div>
        <div><dt>观测窗口 · UTC+8</dt><dd class="num"><time :datetime="issue.affected.start">{{ issue.affected.start }}</time> <span class="window-hours">13:00–14:00</span></dd></div>
        <div><dt>处理人</dt><dd>{{ issue.handler === operatorId ? '演示管理员' : '尚未分配' }}</dd></div>
      </dl>
    </section>

    <div class="quality-columns">
      <section class="panel revision-panel" aria-labelledby="revision-title">
        <div class="panel-head revision-head">
          <div>
            <h2 id="revision-title">修订对照</h2>
            <p v-if="issue.code === 'unordered_conflict'" class="muted">同一观测出现两个值。候选缺少可排序的来源版本。</p>
            <p v-else class="muted">同一来源版本出现异值。批次回执已确认有兼容的更新版本。</p>
          </div>
          <span class="tag" :class="issue.disputed ? 'amber' : 'green'" data-testid="quality-dispute">
            <Lock v-if="issue.disputed" aria-hidden="true" />
            <CircleCheck v-else aria-hidden="true" />
            {{ issue.disputed ? '观测隔离中' : '已解除隔离' }}
          </span>
        </div>
        <div class="revisions" :class="{ 'is-disputed': issue.disputed }">
          <section v-if="currentRevision" class="revision" aria-label="当前修订">
            <div class="revision-role"><h3>当前修订</h3><span class="mono">#{{ currentRevision.revision_id }}</span></div>
            <p class="revision-value"><strong class="num">{{ currentRevision.value }}</strong><span>{{ currentRevision.unit }}</span></p>
            <dl>
              <div><dt>来源版本</dt><dd class="mono">v{{ currentRevision.source_token }}</dd></div>
              <div><dt>应用资格</dt><dd>{{ issue.disputed ? '隔离中' : '可按当前使用许可应用' }}</dd></div>
            </dl>
          </section>
          <section v-if="candidateRevision" class="revision revision-candidate" aria-label="争议候选">
            <div class="revision-role"><h3>争议候选</h3><span class="mono">#{{ candidateRevision.revision_id }}</span></div>
            <p class="revision-value"><strong class="num">{{ candidateRevision.value }}</strong><span>{{ candidateRevision.unit }}</span></p>
            <dl>
              <div><dt>来源版本</dt><dd v-if="candidateRevision.source_token" class="mono">v{{ candidateRevision.source_token }}</dd><dd v-else>不可排序</dd></div>
              <div><dt>证据状态</dt><dd>{{ issue.candidate_revisions.length ? '保留为候选' : '解除争议，保留证据' }}</dd></div>
            </dl>
          </section>
        </div>
        <dl class="version-bar">
          <div><dt>可信版本水位</dt><dd class="mono">v{{ issue.watermark }}</dd></div>
          <div><dt>资格版本</dt><dd class="mono">{{ issue.qualification_version }}</dd></div>
          <div><dt>规则版本</dt><dd class="mono">{{ issue.rule_version }}</dd></div>
          <div><dt>时段口径</dt><dd>小时 · 右端标记</dd></div>
        </dl>
      </section>

      <aside class="quality-stack" aria-label="处置与证据">
        <section class="panel action-panel" aria-labelledby="action-heading">
          <div class="panel-head mini-head">
            <h2 id="action-heading" ref="actionHeading" tabindex="-1">处置操作</h2>
            <span class="muted event-version">事件版本 <b class="mono">{{ issue.event_version }}</b></span>
          </div>
          <div class="panel-body action-panel-body">
            <div v-if="feedback" class="decision-receipt" role="status">
              <CircleCheck aria-hidden="true" />
              <div>{{ feedback }}</div>
            </div>
            <p
              id="quality-action-notice"
              class="muted action-note"
              :class="{ 'denial-note': !canDecide && issue.status !== 'resolved' }"
              :tabindex="!canDecide || issue.status === 'resolved' ? 0 : undefined"
            >
              <Lock v-if="!canDecide && issue.status !== 'resolved'" aria-hidden="true" />
              {{ actionNotice }}
            </p>
            <template v-if="issue.status !== 'resolved'">
              <button
                class="btn primary primary-decision"
                type="button"
                :disabled="!canDecide"
                aria-describedby="quality-action-notice"
                @click="openAction(primaryAction, $event)"
              >
                {{ actions[primaryAction].label }}<ArrowRight aria-hidden="true" />
              </button>
              <div class="action-secondary">
                <button v-if="canClaim && primaryAction !== 'claim'" class="btn subtle" type="button" :disabled="!canDecide" @click="openAction('claim', $event)">领取事件</button>
                <button v-if="canHold && primaryAction !== 'hold'" class="btn subtle" type="button" :disabled="!canDecide" @click="openAction('hold', $event)">保留隔离</button>
                <button v-if="canClose" class="btn subtle" type="button" :disabled="!canDecide" @click="openAction('close', $event)">关闭事件</button>
              </div>
            </template>
            <dl v-if="lastAction" class="receipt-meta">
              <div><dt>最近操作回执</dt><dd class="mono">{{ lastAction.response.action_id }}</dd></div>
              <div><dt>处理时间</dt><dd class="num">5 月 16 日 {{ timeLabel(lastAction.at) }} · UTC+8</dd></div>
            </dl>
          </div>
        </section>

        <section class="panel provenance-panel" aria-labelledby="provenance-heading">
          <div class="panel-head mini-head"><h2 id="provenance-heading">证据与影响</h2><Document class="history-icon" aria-hidden="true" /></div>
          <dl class="provenance-list">
            <div><dt>站码 / 站址期</dt><dd class="mono">{{ issue.affected.object_code }} / #{{ issue.affected.site_epoch_id }}</dd></div>
            <div><dt>异常批次 / 回执</dt><dd class="mono">#{{ issue.batch }} / #{{ issue.receipt }}</dd></div>
            <div><dt>异常批次获取时间</dt><dd class="num">{{ timeLabel(example.receiptTime) }} · UTC+8</dd></div>
            <div><dt>水位证据</dt><dd class="mono">回执 #{{ example.trustedReceipt }}</dd></div>
            <div><dt>采集 Run</dt><dd>未关联 · 本地夹具</dd></div>
          </dl>
          <div class="impact-note">
            <strong>影响范围</strong>
            <p>江湾站这一时槽的 PM2.5 涉及观测明细、分析、支撑明细和受控导出。</p>
          </div>
        </section>
      </aside>

      <section class="panel history-panel" aria-labelledby="quality-history">
        <div class="panel-head history-head">
          <div><h2 id="quality-history">处理记录</h2><p class="muted">合成证据与本页操作回执 · UTC+8</p></div>
          <Document aria-hidden="true" class="history-icon" />
        </div>
        <ol class="history-list">
          <li>
            <time :datetime="issue.created_at" class="num">{{ timeLabel(issue.created_at) }}</time>
            <div><strong>记录{{ title }}</strong><span>保留修订证据，隔离该观测。</span></div>
          </li>
          <li v-if="issue.code === 'same_version_conflict'">
            <time datetime="2024-05-16T06:48:00Z" class="num">14:48</time>
            <div><strong>可信新版进入账本</strong><span>版本 v176 的回执支持重新核验资格。</span></div>
          </li>
          <li v-for="record in example.localActions" :key="record.response.action_id" class="history-action">
            <time :datetime="record.at" class="num">{{ timeLabel(record.at) }}</time>
            <div>
              <strong>{{ actions[record.command.action].label }} · {{ statusText[record.response.status] }}</strong>
              <span>演示管理员 · {{ actions[record.command.action].reasonText }} · 事件版本 {{ record.response.event_version }}</span>
            </div>
          </li>
        </ol>
      </section>
    </div>

    <ElDialog
      v-model="dialogOpen"
      :title="`确认${selectedAction.label}`"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :close-on-press-escape="!pending"
      :show-close="!pending"
      append-to-body
      trap-focus
      destroy-on-close
      @closed="restoreFocus"
    >
      <form class="decision-form" novalidate @submit.prevent="submit">
        <p>{{ impact[activeAction] }}</p>
        <dl class="decision-context">
          <div><dt>事件及控制版本</dt><dd class="mono">#{{ issue.id }} · {{ command?.expected_version }}</dd></div>
          <div><dt>预计工单状态</dt><dd>{{ statusText[issue.status] }} → {{ statusText[previewStatus] }}</dd></div>
          <div><dt>作用对象</dt><dd>江湾站 · PM2.5 · 14:00（UTC+8）</dd></div>
        </dl>
        <label for="quality-reason">处置理由 <span class="muted">（必选）</span></label>
        <select id="quality-reason" ref="reasonField" v-model="reason" required :disabled="pending" :aria-invalid="Boolean(error && !reason)" :aria-describedby="error && !reason ? 'quality-action-error' : undefined">
          <option disabled value="">请选择处置理由</option>
          <option :value="selectedAction.reason">{{ selectedAction.reasonText }}</option>
        </select>
        <p v-if="error" id="quality-action-error" ref="dialogError" class="decision-error" role="alert" tabindex="-1">{{ error }}</p>
        <div class="decision-actions">
          <button class="btn subtle" type="button" :disabled="pending" @click="dialogOpen = false">取消</button>
          <button class="btn primary" type="submit" :disabled="pending">{{ pending ? '正在提交…' : '确认提交' }}</button>
        </div>
      </form>
    </ElDialog>
  </div>
</template>

<style scoped>
.quality-page { display: grid; gap: 20px; min-width: 0; }
.quality-title { margin-bottom: 0; }
.quality-title .title-copy p { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.title-separator { width: 3px; height: 3px; border-radius: 50%; background: currentColor; }
.sample-select { color: var(--muted); font-size: 13px; }
.quality-title select, .decision-form select {
  color: inherit; min-height: 38px; border: 1px solid var(--line); border-radius: 7px;
  background-color: var(--surface); padding: 0 10px; font: inherit; font-size: 13px;
}
.context-status { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 13px 20px; border-bottom: 1px solid var(--line); }
.context-code { margin-left: 5px; font-size: 11px; }
.context-status .tag svg, .revision-head .tag svg { width: 13px; height: 13px; flex: none; }
.context-grid { display: grid; grid-template-columns: 1.05fr 1fr 1.35fr .85fr; margin: 0; }
.context-grid > div { min-width: 0; padding: 13px 20px 15px; }
.context-grid > div + div { border-left: 1px solid var(--line); }
.context-grid dt, .version-bar dt { color: var(--muted); font-size: 12px; }
.context-grid dd, .version-bar dd { margin: 4px 0 0; font-weight: 600; font-size: 14px; }
.context-reference { margin-left: 8px; color: var(--muted); font-size: 12px; font-weight: 400; }
.window-hours { white-space: nowrap; }
.quality-columns { display: grid; grid-template-columns: minmax(0, 1fr) minmax(296px, 340px); grid-template-rows: max-content minmax(0, 1fr); align-items: start; gap: 20px; }
.quality-columns > *, .quality-stack section { min-width: 0; }
.quality-stack { grid-area: 1 / 2 / span 2; display: grid; gap: 20px; }
.history-panel { grid-area: 2 / 1; }
.revision-head, .history-head, .mini-head { padding: 18px 20px; }
.revision-head { align-items: flex-start; gap: 12px; }
.revision-head > div { min-width: 0; }
.revision-head h2, .history-head h2, .mini-head h2 { margin: 0; font-size: 17px; line-height: 1.4; }
.revision-head p, .history-head p { font-size: 13px; margin: 5px 0 0; line-height: 1.6; }
.revisions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0 20px 16px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.revision { min-width: 0; padding: 14px 18px 16px; background: #f8fafc; }
.revision-candidate { background: var(--surface); border-left: 1px solid var(--line); }
.is-disputed .revision-candidate { background: #fffcf6; }
.revision-role { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; align-items: baseline; }
.revision-role h3 { font-size: 14px; margin: 0; }
.revision-role > span { font-size: 11px; color: var(--muted); }
.revision-value { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin: 7px 0 8px; }
.revision-value strong { font-size: 38px; line-height: 1.15; font-weight: 650; letter-spacing: -.035em; }
.revision-value span { color: var(--muted); font-size: 13px; }
.revision dl, .receipt-meta { display: grid; gap: 4px; margin: 0; }
.revision dl > div { display: flex; flex-wrap: wrap; gap: 4px 16px; justify-content: space-between; }
.revision dt, .revision dd, .receipt-meta dt, .receipt-meta dd { margin: 0; font-size: 12px; }
.revision dt, .receipt-meta dt { color: var(--muted); }
.version-bar { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 12px; padding: 12px 20px 14px; margin: 0; border-top: 1px solid var(--line); }
.version-bar dd { font-size: 13px; overflow-wrap: anywhere; }
.history-icon { width: 17px; height: 17px; color: var(--muted); }
.history-list { list-style: none; padding: 0 20px 15px; margin: 0; }
.history-list li { display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 0 11px; }
.history-list time { color: var(--muted); font-size: 12px; padding: 11px 0 0; }
.history-list li > div { position: relative; padding: 9px 0 12px 16px; border-left: 1px solid var(--line); display: grid; gap: 2px; }
.history-list li > div::before { content: ''; position: absolute; left: -4px; top: 15px; width: 7px; height: 7px; border-radius: 50%; background: #8494a9; }
.history-list .history-action > div::before { background: var(--brand); }
.history-list strong { font-size: 13px; font-weight: 600; }
.history-list span { font-size: 12px; color: var(--muted); }
.mini-head { border-bottom: 1px solid var(--line); align-items: center; gap: 6px; }
.event-version { font-size: 12px; white-space: nowrap; }
.event-version b { color: var(--ink); margin-left: 4px; }
.action-panel-body { display: grid; gap: 13px; padding: 15px 19px 18px; }
.action-note { margin: 0; font-size: 13px; line-height: 1.65; }
.denial-note { display: flex; align-items: flex-start; gap: 8px; color: #8b4d13; }
.denial-note svg { width: 15px; height: 15px; flex: none; margin-top: 3px; }
.primary-decision { width: 100%; justify-content: space-between; }
.primary-decision svg { width: 14px; height: 14px; flex: none; }
.action-secondary { display: flex; flex-wrap: wrap; gap: 8px; }
.action-secondary .btn { flex: 1 1 auto; justify-content: center; }
.decision-receipt { background: #edf7f1; color: #1d633d; border-radius: 6px; display: flex; gap: 8px; align-items: flex-start; padding: 9px 10px; font-size: 13px; }
.decision-receipt svg { width: 15px; height: 15px; flex: none; margin-top: 2px; }
.receipt-meta > div { display: grid; gap: 2px; }
.receipt-meta dd { overflow-wrap: anywhere; }
.provenance-list { margin: 0; padding: 5px 19px 9px; }
.provenance-list > div { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 10px; padding: 7px 0; }
.provenance-list > div + div { border-top: 1px solid var(--line); }
.provenance-list dt, .provenance-list dd { margin: 0; font-size: 12px; }
.provenance-list dt { color: var(--muted); }
.impact-note { border-top: 1px solid var(--line); margin: 0; padding: 12px 19px 15px; }
.impact-note strong { font-size: 13px; }
.impact-note p { font-size: 12px; margin: 4px 0 0; color: var(--muted); line-height: 1.65; }
.decision-form { font-size: 14px; color: var(--ink); }
.decision-form > p { margin: 0 0 14px; }
.decision-context { border: 1px solid var(--line); background: #f8fafc; border-radius: 7px; margin: 0 0 16px; padding: 10px 12px; display: grid; gap: 7px; }
.decision-context > div { display: grid; grid-template-columns: minmax(100px, .7fr) minmax(0, 1.3fr); gap: 8px; }
.decision-context dt, .decision-context dd { margin: 0; font-size: 13px; }
.decision-context dt { color: var(--muted); }
.decision-form > label { display: block; font-weight: 600; margin-bottom: 6px; }
.decision-form select { display: block; min-height: 44px; width: 100%; }
.decision-form .decision-error { margin: 8px 0 0; color: #9e251d; }
.decision-actions { display: flex; gap: 8px; justify-content: end; margin-top: 18px; }
@media (max-width: 845px) {
  .quality-columns { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto; }
  .quality-stack { grid-area: auto; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
  .history-panel { grid-area: auto; }
}
@media (max-width: 620px) {
  .quality-page, .quality-stack, .quality-columns { gap: 14px; }
  .context-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .context-grid > div { padding: 10px 14px 12px; }
  .context-grid > div:nth-child(3) { border-left: none; }
  .context-grid > div:nth-child(n + 3) { border-top: 1px solid var(--line); }
  .context-grid dd { font-size: 13px; }
  .quality-title .actions { flex-wrap: wrap; width: 100%; }
  .quality-title select { min-height: 44px; flex: 1 1 auto; min-width: 0; }
  .revision-head, .history-head, .mini-head, .context-status { padding: 13px 14px; }
  .revisions { margin: 0 14px 12px; }
  .revision { padding: 10px 11px 12px; }
  .revision-candidate { padding-left: 12px; }
  .revision-value strong { font-size: 34px; }
  .version-bar { padding: 10px 14px 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .history-list { padding-inline: 14px; }
  .quality-stack { grid-template-columns: minmax(0, 1fr); }
  .action-secondary .btn, .primary-decision, .decision-actions .btn { min-height: 44px; }
}
@media (max-width: 390px) {
  .context-reference { margin-left: 4px; }
  .revision-head { flex-wrap: wrap; }
  .revision dl > div { display: grid; gap: 2px; }
  .revision-value { gap: 4px; }
  .revision-value span { font-size: 12px; }
}
</style>
