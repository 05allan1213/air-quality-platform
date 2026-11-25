<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, usePrivateRead } from '../api/client'
import { label, timeText, type EvidencePage, type EvidenceSelector, type Revision } from './qualityContract'

const props = defineProps<{ product: number; synthetic: boolean; batch: number; keyType?: 'city' | 'station'; revision: number; receipt?: number | null }>()
const { data, error, busy, run, clear } = usePrivateRead<EvidencePage & { synthetic: boolean }>()
const selector = ref<EvidenceSelector>({batch: props.batch})
const from = ref<number | undefined>()
const batchMode = computed(() => 'batch' in selector.value)
const rows = computed(() => data.value?.revisions ?? [])
const batchRows = computed(() => data.value?.receipts ?? [])
function read(next: EvidenceSelector, after?: number) {
  selector.value = next
  from.value = after
  return run(async signal => {
    const result = await api.get<EvidencePage & { synthetic: boolean }>('admin/observation-evidence/', { product: props.product, scope: props.synthetic ? 'synthetic' : undefined, ...next, after }, signal)
    if (result.synthetic !== props.synthetic) throw new Error('Evidence context mismatch')
    return result
  })
}
function openKey(id: number) {
  if (props.keyType === 'city') void read({city_key: id})
  if (props.keyType === 'station') void read({station_key: id})
}
function reading(row: Revision | undefined) {
  if (!row) return '未披露'
  if (row.censor_limit != null) return `${row.censor_direction}${row.censor_limit} ${row.unit}`
  return row.value == null ? '无数值' : `${row.value} ${row.unit}`
}
watch(() => [props.product, props.batch, props.synthetic, props.revision, props.keyType], () => {
  clear()
  selector.value = {batch: props.batch}
  from.value = undefined
}, { flush: 'sync' })
onMounted(() => void read({batch: props.batch}))
</script>

<template>
  <section class="panel quality-section" aria-labelledby="quality-evidence-title" :aria-busy="busy">
    <div class="quality-bar"><div><h2 id="quality-evidence-title" tabindex="-1">批次与观测证据</h2><p>批次 {{ batch }}<template v-if="receipt"> · 关联回执 {{ receipt }}</template> · 仅显示获权修订及来源字段。</p></div><button class="btn" type="button" :disabled="busy" @click="read({batch})">{{ data ? '回读批次证据' : '读取批次证据' }}</button></div>
    <p v-if="busy" role="status">正在核验修订证据…</p>
    <p v-else-if="error" class="status status-error" role="alert">当前证据不可访问，或未提供可披露的关联记录。</p>
    <template v-else-if="data">
      <p class="quality-context">{{ data.attribution }}<template v-if="data.fetched_at"> · 抓取时刻 {{ timeText(data.fetched_at) }}</template><template v-if="data.profile"> · {{ data.profile }}</template></p>
      <p v-if="batchMode && !batchRows.length" role="status">当前获权页没有修订回执；批次问题不代表有已认可观测。</p>
      <div v-if="batchMode && batchRows.length" class="quality-scroll" role="region" tabindex="0" aria-label="批次回执表"><table><caption>获权批次回执</caption><thead><tr><th scope="col">回执</th><th scope="col">结果</th><th scope="col">修订与读数</th><th scope="col">观测证据</th></tr></thead><tbody><tr v-for="entry in batchRows" :key="entry.receipt_id" :class="{ 'quality-selected': receipt === entry.receipt_id }"><th scope="row">{{ entry.receipt_id }}<small v-if="receipt === entry.receipt_id">本事件关联</small></th><td>{{ label(entry.outcome) }}</td><td>{{ entry.revision?.revision_id ?? '未披露' }} · {{ reading(entry.revision) }}<small v-if="entry.revision">{{ label(entry.revision.quality) }} · 规则 {{ entry.revision.rule }}<template v-if="entry.revision.candidate"> · 候选</template></small></td><td><button v-if="keyType && entry.key_id" class="btn quality-small" type="button" @click="openKey(entry.key_id)">读取观测键 {{ entry.key_id }}</button><span v-else>键类型待核</span></td></tr></tbody></table></div>
      <template v-if="!batchMode">
        <p>来源水位 {{ data.watermark ?? '未披露' }} · {{ data.disputed ? '仍有争议' : '当前键未标记争议' }}</p>
        <div class="quality-scroll" role="region" tabindex="0" aria-label="观测修订表"><table><caption>观测键修订</caption><thead><tr><th scope="col">修订</th><th scope="col">资格</th><th scope="col">读数</th><th scope="col">依据</th></tr></thead><tbody><tr v-for="row in rows" :key="row.revision_id"><th scope="row">{{ row.revision_id }} / 第 {{ row.revision_no }} 版</th><td>{{ label(row.quality) }} · {{ row.current ? '当前' : row.candidate ? '候选' : '非当前' }}</td><td>{{ reading(row) }}<small v-if="row.raw_value != null">获权原值 {{ row.raw_value }} {{ row.raw_unit }}</small></td><td>{{ label(row.kind) }} · 规则 {{ row.rule }} · 来源版本 {{ row.source_token ?? '未披露' }}<small v-if="row.source_time">来源时间 {{ row.source_time }}</small><small v-if="row.source_description">{{ row.source_description }}</small></td></tr></tbody></table></div>
      </template>
      <p v-if="data.issues.length">问题代码：{{ data.issues.map(code => label(code)).join('、') }}</p>
      <div class="quality-actions"><button v-if="data.next_after != null" class="btn" type="button" @click="read(selector, data.next_after)">下一页证据</button><button v-if="from != null" class="btn" type="button" @click="read(selector)">证据首页</button></div>
    </template>
    <p v-else>批次不一定包含当前修订或全部候选。只有获权回执带回观测键且键类型明确时，才能继续查该键。</p>
  </section>
</template>
