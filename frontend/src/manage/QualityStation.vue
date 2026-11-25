<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { ApiError, api, privateStateGeneration, usePrivateRead } from '../api/client'
import { actionReasons, numericId, timeText, type StationAction, type StationProof, type StationReceipt } from './qualityContract'
import { useQualityCommand } from './qualityCommand'

const read = usePrivateRead<StationProof>()
const command = useQualityCommand('station_change')
const station = ref('')
const product = ref('')
const identifier = ref('')
const at = ref('')
const target = ref('')
const action = ref<StationAction>('rename')
const receipt = ref<StationReceipt | null>(null)
const error = ref('')
const confirmButton = ref<HTMLButtonElement | null>(null)
const path = computed(() => numericId(station.value) ? `admin/stations/${numericId(station.value)}/changes/` : '')
const proof = read.data
const stationId = computed(() => numericId(station.value))
const waiting = computed(() => stationId.value ? command.pendingId(stationId.value) : undefined)
const stationLabels: Record<StationAction, string> = { rename: '更名', relocate: '迁址', reuse_code: '复用代码' }

function clearFeedback() {
  read.clear()
  receipt.value = null
  error.value = ''
}
function clearPrivate() {
  station.value = product.value = identifier.value = at.value = target.value = ''
  action.value = 'rename'
  clearFeedback()
}
watch([station, product, identifier, at, target, action], clearFeedback, { flush: 'sync' })
window.addEventListener('air-quality:clear-reads', clearPrivate)
onScopeDispose(() => window.removeEventListener('air-quality:clear-reads', clearPrivate))

async function preview() {
  const productId = numericId(product.value), code = numericId(identifier.value)
  const atValue = at.value.trim()
  const targetValue = target.value.trim()
  error.value = ''
  receipt.value = null
  read.clear()
  if (!path.value || !productId || !code || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/.test(atValue)
    || !Number.isFinite(Date.parse(atValue)) || !targetValue || (action.value !== 'rename' && !numericId(targetValue))) {
    error.value = '填写受控交接的正整数标识、含时区生效时刻和该动作的目标。'
    return
  }
  const result = await read.run(async signal => {
    const value = await api.get<StationProof>(path.value, {product: productId, action: action.value, identifier: code, at: atValue, target: targetValue}, signal)
    if (!value.affected.synthetic || value.affected.product !== productId) throw new Error('Station context mismatch')
    return value
  })
  if (result) {
    await nextTick()
    confirmButton.value?.focus()
  }
}

async function submit() {
  const value = proof.value
  const targetId = stationId.value
  if (!value || !path.value || !targetId || waiting.value) return
  const generation = privateStateGeneration()
  const verb = action.value
  error.value = ''
  try {
    const result = await command.submit<StationReceipt>(targetId, path.value, {
      action: verb, reason_code: actionReasons[verb], expected_version: value.version,
      evidence_ref: value.evidence_ref,
    })
    if (generation !== privateStateGeneration()) return
    if (!result) {
      error.value = '命令结果尚未确认；请核对原回执，不要重复提交。'
      return
    }
    read.clear()
    receipt.value = result
  } catch (cause) {
    if (generation !== privateStateGeneration()) return
    read.clear()
    error.value = cause instanceof ApiError && cause.status === 409
      ? '目录版本已变化。请按当前状态重新预览，旧方案不会重放。'
      : cause instanceof ApiError && (cause.status === 0 || cause.status >= 500)
        ? '未收到确定回执；系统保留原请求并禁止重复提交。请查询原回执。'
        : cause instanceof ApiError ? cause.message : '没有收到站址回执。'
  }
}

async function lookupReceipt() {
  const targetId = stationId.value
  if (!targetId) return
  const generation = privateStateGeneration()
  try {
    const result = await command.lookup(targetId)
    if (result && generation === privateStateGeneration()) {
      read.clear()
      receipt.value = { action_id: result.action_id, status: result.state as StationReceipt['status'], version: result.version }
      error.value = '已核对到原始命令回执。请重新预览当前目录后再决定是否继续。'
    }
  } catch (cause) {
    if (generation !== privateStateGeneration()) return
    error.value = cause instanceof ApiError && cause.status === 404
      ? '尚未找到可披露的原始回执；原命令仍锁定，禁止另发新命令。'
      : cause instanceof ApiError ? cause.message : '原始回执查询失败。'
  }
}
</script>

<template>
  <section class="panel quality-section" aria-labelledby="quality-station-title">
    <div class="quality-bar"><div><h2 id="quality-station-title">站址治理</h2><p>使用受控交接的站点和标识主键。正式来源没有可用的目录证据入口。</p></div></div>
    <form class="quality-form" @submit.prevent="preview">
      <fieldset :disabled="command.busy.value || read.busy.value">
        <legend>站址变更意图（F）</legend>
        <div class="quality-fields">
          <label class="field">站点 ID<input v-model.trim="station" inputmode="numeric" required /></label>
          <label class="field">站址产品 ID<input v-model.trim="product" inputmode="numeric" required /></label>
          <label class="field">站点标识 ID<input v-model.trim="identifier" inputmode="numeric" required /></label>
          <label class="field">目录动作<select v-model="action"><option v-for="(name, key) in stationLabels" :key="key" :value="key">{{ name }}</option></select></label>
          <label class="field">生效时刻（含时区）<input v-model.trim="at" placeholder="2024-02-02T10:00+08:00" required /></label>
          <label class="field">{{ action === 'rename' ? '新名称' : action === 'relocate' ? '目标城市 ID' : '目标站址期 ID' }}<input v-model.trim="target" :inputmode="action === 'rename' ? 'text' : 'numeric'" maxlength="100" required /></label>
        </div>
        <button class="btn" type="submit">{{ read.busy.value ? '核验中…' : '预览站址变更' }}</button>
      </fieldset>
    </form>
    <p v-if="error || read.error.value" class="status status-error" role="alert">{{ error || read.error.value?.message }}</p>
    <div v-if="waiting" class="status status-warning" role="status">未收到请求 {{ waiting }} 的回执。<button class="btn quality-small" type="button" :disabled="command.busy.value" @click="lookupReceipt">只查询原始回执</button><span>不重复提交。</span></div>
    <div v-if="proof" class="quality-chain">
      <h3>确认{{ stationLabels[action] }}</h3>
      <p>站点 {{ station }} · 产品 {{ proof.affected.product }} · 目录版本 v{{ proof.version }} · 目标 {{ target }}</p>
      <p>生效范围：{{ timeText(proof.affected.start) }} 至 {{ proof.affected.end ?? '无上界' }}（右端不含）。</p>
      <ul><li v-for="span in proof.affected.scope" :key="`${span.geography}-${span.object_code}-${span.start}`">{{ span.geography }} / {{ span.object_code }} · {{ span.start }} 至 {{ span.end }}</li></ul>
      <div class="quality-actions"><button ref="confirmButton" class="btn primary" type="button" :disabled="command.busy.value || Boolean(waiting)" @click="submit">确认提交站址变更</button><button class="btn" type="button" :disabled="command.busy.value" @click="read.clear()">取消预览</button></div>
    </div>
    <div v-if="receipt" class="quality-chain" role="status"><h3>站址回执</h3><p>{{ receipt.status === 'applied' ? '目录变更已应用' : '映射待核，目录变更未应用' }} · 目录版本 v{{ receipt.version }}</p><p>回执 {{ receipt.action_id }}</p><p v-if="receipt.affected?.new_epoch != null">原站址期 {{ receipt.affected.old_epoch }} → 当前结果站址期 {{ receipt.affected.new_epoch }}。历史观测仍按原身份核对。</p></div>
  </section>
</template>
