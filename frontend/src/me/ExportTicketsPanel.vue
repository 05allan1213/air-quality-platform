<script setup lang="ts">
import { onScopeDispose, ref } from 'vue'
import { Download, RefreshRight } from '@element-plus/icons-vue'
import { ApiError, api } from '../api/client'
import { scopeQuery, useSessionStore } from '../stores/session'
import OwnedState from './OwnedState.vue'
import { timeLabel, useOwnedPage } from './owned'

type Ticket = { id: string; kind: 'detail' | 'aggregate'; created_at: string; expires_at: string }

const session = useSessionStore()
const now = ref(Date.now())
const unavailable = ref<Record<string, string>>({})
const removeId = ref('')
let activeDownload: AbortController | undefined

function cancelDownload() {
  activeDownload?.abort()
  activeDownload = undefined
}
const { data, busy, error, notice, actionError, actionBusy, cursor, list, item, act } = useOwnedPage<Ticket>('exports', () => {
  cancelDownload()
  unavailable.value = {}
  removeId.value = ''
})
const clock = window.setInterval(() => { now.value = Date.now() }, 1000)
onScopeDispose(() => {
  cancelDownload()
  window.clearInterval(clock)
})

function reload(next?: string) {
  removeId.value = ''
  void list(next)
}

function status(ticket: Ticket) {
  return unavailable.value[ticket.id] ?? (Date.parse(ticket.expires_at) <= now.value ? '票据已过期' : '下载时核验')
}

async function download(ticket: Ticket) {
  if (actionBusy.value) return
  const controller = new AbortController()
  activeDownload = controller
  await act(() => api.downloadCsv(ticket.id, session.scope, controller.signal), () => { notice.value = 'CSV 下载已开始。' }, cause => {
    if (!(cause instanceof ApiError)) return
    const states: Record<string, string> = {
      expired: '票据已过期', method_changed: '导出方法已更新', input_changed: '数据版本已变化',
      use_denied: '当前用途未获授权', permission_denied: '当前不可下载', not_found: '当前不可下载',
    }
    if (states[cause.code]) unavailable.value[ticket.id] = states[cause.code]
    if (cause.code === 'not_found') reload()
  })
  if (activeDownload === controller) activeDownload = undefined
}

function remove(id: string) {
  void act(() => api.delete(item(id)), () => {
    delete unavailable.value[id]
    notice.value = '下载票据已删除。'
    reload()
  }, cause => { if (cause instanceof ApiError && cause.status === 404) reload() })
}
</script>

<template>
  <section class="me-panel" aria-labelledby="my-exports-title">
    <header class="me-panel-head">
      <div><h2 id="my-exports-title">近期导出</h2><p>下载时重新检查数据版本和适用授权。</p></div>
      <button class="btn" type="button" :disabled="busy || actionBusy" @click="reload()"><RefreshRight aria-hidden="true" />刷新票据</button>
    </header>
    <p v-if="notice" class="status status-success" role="status">{{ notice }}</p>
    <p v-if="actionError" class="status status-error" role="alert">{{ actionError }}</p>
    <OwnedState :busy="busy" :error="error?.message" :empty="data?.results.length === 0" empty-text="还没有下载票据。" @retry="reload()" />
    <RouterLink v-if="data?.results.length === 0" class="btn me-fit" :to="{ name: 'query', query: scopeQuery(session.scope) }">打开数据查询</RouterLink>
    <ul v-if="data?.results.length" class="me-list">
      <li v-for="ticket in data.results" :key="ticket.id" class="me-row">
        <article class="me-entry">
          <div class="me-entry-head">
            <div>
              <h3>{{ ticket.kind === 'detail' ? '明细 CSV' : '汇总 CSV' }}</h3>
              <p>{{ status(ticket) }} · 创建于 {{ timeLabel(ticket.created_at) }}</p>
              <p>到期 <time :datetime="ticket.expires_at">{{ timeLabel(ticket.expires_at) }}</time></p>
            </div>
            <div class="me-actions">
              <button class="btn primary" type="button" :disabled="actionBusy || Date.parse(ticket.expires_at) <= now || !!unavailable[ticket.id]" @click="download(ticket)"><Download aria-hidden="true" />下载 CSV</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="removeId = ticket.id">删除票据</button>
            </div>
          </div>
          <div v-if="removeId === ticket.id" class="me-details">
            <p>确定删除这张下载票据？</p>
            <div class="me-actions">
              <button class="btn" type="button" :disabled="actionBusy" @click="remove(ticket.id)">确认删除票据</button>
              <button class="btn" type="button" :disabled="actionBusy" @click="removeId = ''">取消</button>
            </div>
          </div>
        </article>
      </li>
    </ul>
    <nav v-if="cursor || data?.page.next_cursor" class="me-actions me-pagination" aria-label="票据分页">
      <button v-if="cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload()">返回第一页</button>
      <button v-if="data?.page.next_cursor" class="btn" type="button" :disabled="busy || actionBusy" @click="reload(data.page.next_cursor)">下一页</button>
    </nav>
  </section>
</template>
