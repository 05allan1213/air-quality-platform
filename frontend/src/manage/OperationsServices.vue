<script setup lang="ts">
import { clockText, components, evidenceNames, stateLabel, type ComponentName, type ComponentStates } from './operationsContract'

defineProps<{ states: ComponentStates }>()
const names = Object.keys(components) as ComponentName[]
</script>

<template>
  <ul class="service-list" aria-label="五项组件状态">
    <li v-for="name in names" :key="name" class="service">
      <div class="service-heading">
        <h3>{{ components[name] }}</h3>
        <span class="state" :data-state="states[name].state">{{ stateLabel[states[name].state] }}</span>
      </div>
      <dl class="service-evidence">
        <div><dt>证据</dt><dd>{{ evidenceNames[states[name].evidence_kind] ?? states[name].evidence_kind }}</dd></div>
        <div><dt>观测于</dt><dd><time :datetime="states[name].observed_at ?? undefined">{{ clockText(states[name].observed_at) }}</time></dd></div>
        <div><dt>有效至</dt><dd><time :datetime="states[name].valid_until ?? undefined">{{ clockText(states[name].valid_until) }}</time></dd></div>
        <div v-if="states[name].reason_code"><dt>诊断码</dt><dd>{{ evidenceNames[states[name].reason_code] ?? states[name].reason_code }}</dd></div>
      </dl>
      <dl v-if="name === 'worker'" class="service-evidence worker-evidence">
        <div><dt>队列订阅</dt><dd>{{ stateLabel[states.worker.queue_readiness.state] }} · {{ states.worker.queue_readiness.queue }}</dd></div>
        <div v-if="states.worker.queue_readiness.reason_code"><dt>订阅证据</dt><dd>{{ evidenceNames[states.worker.queue_readiness.reason_code] ?? states.worker.queue_readiness.reason_code }}</dd></div>
        <div><dt>最近消费</dt><dd><time :datetime="states.worker.last_consumption?.observed_at">{{ clockText(states.worker.last_consumption?.observed_at) }}</time><span v-if="states.worker.last_consumption"> · {{ states.worker.last_consumption.queue }}</span></dd></div>
      </dl>
    </li>
  </ul>
</template>

<style scoped>
.service-list { margin: 0; padding: 0; list-style: none; border-block-start: 1px solid var(--aq-border); }
.service { display: grid; grid-template-columns: minmax(145px, 1fr) minmax(0, 4fr); gap: 8px 14px; padding: 14px 0; border-block-end: 1px solid var(--aq-border); }
.service:last-child { border-block-end: 0; padding-bottom: 0; }
.service-heading { display: flex; flex-wrap: wrap; align-items: start; align-content: start; gap: 6px 10px; }
.service-heading h3 { margin: 0; font-size: .96rem; }
.state { padding: .08rem .45rem; border-radius: 6px; font-size: .79rem; font-weight: 650; }
.state[data-state='ok'] { background: var(--aq-success-bg); color: var(--aq-success); }
.state[data-state='unknown'] { background: var(--aq-warning-bg); color: var(--aq-warning); }
.state[data-state='unavailable'] { background: var(--aq-danger-bg); color: var(--aq-danger); }
.service-evidence { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 0; }
.service-evidence div { min-width: 0; }
.service-evidence dt { color: var(--aq-secondary); font-size: .78rem; }
.service-evidence dd { margin: 0; font-size: .85rem; overflow-wrap: anywhere; }
.worker-evidence { grid-column: 2; padding-block-start: 8px; border-block-start: 1px solid var(--aq-border); }
@media (max-width: 550px) {
  .service { grid-template-columns: minmax(0, 1fr); }
  .worker-evidence { grid-column: 1; }
}
</style>
