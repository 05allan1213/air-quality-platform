<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, usePrivateRead } from '../api/client'
import { useSessionStore } from '../stores/session'
import { auditKinds, auditObjectId, auditWindow, clockText, type AuditKind } from './operationsContract'

const props = defineProps<{ kinds: AuditKind[] }>()
const route = useRoute()
const session = useSessionStore()
type Selection = { kind: AuditKind; id: string; start: string; end: string }
type AuditRow = { at: string; action: string; outcome: string; actor_relation: 'self' | 'other' | 'system'; before_version?: number | null; after_version?: number | null }
type AuditPage = { results: AuditRow[]; page: { page_size: number; next_cursor: string | null } }
const relation = { self: '本人', other: '其他操作者', system: '系统' }
const kind = ref<AuditKind | ''>('')
const id = ref('')
const start = ref('')
const end = ref('')
const selected = ref<Selection | null>(null)
const rows = ref<AuditRow[]>([])
const cursor = ref<string | null>(null)
const notice = ref('')
const read = usePrivateRead<AuditPage>()
const dateFields = computed(() => auditWindow(start.value, end.value))

function resetRead() {
  read.clear()
  selected.value = null
  rows.value = []
  cursor.value = null
  notice.value = ''
}

function resetAll() {
  kind.value = props.kinds[0] ?? ''
  id.value = ''
  start.value = ''
  end.value = ''
  resetRead()
}

function updateFromRoute() {
  resetAll()
  const linked = props.kinds.find(item => item === route.query.audit_kind)
  if (linked && typeof route.query.audit_id === 'string' && auditObjectId(linked, route.query.audit_id)) {
    kind.value = linked
    id.value = route.query.audit_id
  }
}

watch(() => [props.kinds.join(','), route.query.audit_kind, route.query.audit_id], updateFromRoute, { immediate: true })
watch([kind, id, start, end], resetRead, { flush: 'sync' })
window.addEventListener('air-quality:clear-reads', resetAll)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', resetAll))

async function load(selection: Selection, token?: string) {
  const result = await read.run(signal => api.get<AuditPage>('admin/object-audit/', {
    ...selection, page_size: 25, cursor: token,
  }, signal))
  if (result) {
    rows.value = token ? [...rows.value, ...result.results] : result.results
    cursor.value = result.page.next_cursor
  } else if (read.error.value) {
    const error = read.error.value
    resetRead()
    notice.value = error.status === 403 || error.status === 404
      ? '当前无法披露此对象的审计记录。'
      : error.code === 'invalid_cursor' || error.status === 409
        ? '分页依据已变化，请从第一页重新读取。'
        : error.message
    if (error.code === 'permission_denied') void session.refresh().catch(() => undefined)
  }
}

async function submit() {
  resetRead()
  if (!kind.value || !props.kinds.includes(kind.value) || !auditObjectId(kind.value, id.value)) {
    notice.value = '请填入已知对象的有效编号。运行对象使用 UUID。'
    return
  }
  if (!dateFields.value) { notice.value = '请选择有效的 UTC 起止时间，范围最多 30 天。'; return }
  selected.value = { kind: kind.value, id: id.value, ...dateFields.value }
  await load(selected.value)
}

function more() {
  if (selected.value && cursor.value) void load(selected.value, cursor.value)
}
</script>

<template>
  <section class="audit" aria-labelledby="object-audit-title">
    <h2 id="object-audit-title">对象审计</h2>
    <p class="hint">按一个已知对象核对获准的治理经过。时间使用 UTC，结束时刻不含在区间内，最多 30 天。</p>
    <form class="audit-form" @submit.prevent="submit">
      <label>审计对象类型<select v-model="kind"><option v-for="item in kinds" :key="item" :value="item">{{ auditKinds[item] }}</option></select></label>
      <label>审计对象编号<input v-model.trim="id" :inputmode="kind === 'run' ? 'text' : 'numeric'" maxlength="36" autocomplete="off" required></label>
      <label>审计开始 UTC<input v-model="start" type="datetime-local" required></label>
      <label>审计结束 UTC<input v-model="end" type="datetime-local" required></label>
      <button type="submit" class="btn" :disabled="read.busy.value">查看对象审计</button>
    </form>
    <p v-if="notice" class="status status-warning" role="alert">{{ notice }}</p>
    <p v-else-if="read.busy.value" class="hint" role="status">正在读取对象审计…</p>
    <p v-else-if="selected && !rows.length" class="hint" role="status">所选区间没有可披露的审计记录。</p>
    <div v-if="rows.length" class="audit-table" tabindex="0" role="region" aria-label="对象审计记录，可局部滚动">
      <table>
        <caption>获权的对象审计记录</caption>
        <thead><tr><th scope="col">时刻</th><th scope="col">动作</th><th scope="col">结果</th><th scope="col">操作者关系</th><th scope="col">版本变化</th></tr></thead>
        <tbody><tr v-for="(row, index) in rows" :key="`${row.at}:${index}`">
          <td><time :datetime="row.at">{{ clockText(row.at) }}</time></td><td>{{ row.action }}</td><td>{{ row.outcome }}</td>
          <td>{{ relation[row.actor_relation] }}</td><td>{{ row.before_version ?? '—' }} → {{ row.after_version ?? '—' }}</td>
        </tr></tbody>
      </table>
    </div>
    <button v-if="cursor" class="btn more" type="button" :disabled="read.busy.value" @click="more">继续读取审计</button>
  </section>
</template>

<style scoped>
.audit { min-width: 0; padding: 20px 0; border-bottom: 1px solid var(--aq-border); }
h2 { margin-bottom: 0; }
.hint { margin: 4px 0 12px; color: var(--aq-secondary); font-size: .88rem; }
.audit-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(160px, 100%), 1fr)); align-items: end; gap: 10px; }
label { display: grid; min-width: 0; gap: 4px; color: var(--aq-secondary); font-size: .81rem; font-weight: 620; }
input, select { width: 100%; min-width: 0; min-height: 40px; padding: 6px 8px; border: 1px solid var(--aq-control-border); border-radius: 6px; background: var(--aq-content); color: var(--aq-text); }
.audit-table { margin-top: 12px; overflow: auto; border: 1px solid var(--aq-border); }
table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: .84rem; }
th, td, caption { padding: 8px 12px; text-align: start; }
th, td { border-top: 1px solid var(--aq-border); }
.more, .status { margin-top: 10px; }
</style>
