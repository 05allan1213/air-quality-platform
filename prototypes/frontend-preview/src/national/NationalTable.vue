<script setup lang="ts">
import { ElOption, ElSelect } from 'element-plus'
import 'element-plus/es/components/select/style/css'
import { computed, ref } from 'vue'
import { Check, ArrowRight, ArrowLeft, Search, RefreshLeft, SortDown, SortUp } from '@element-plus/icons-vue'
import { cities, filteredCities, grades, provinces, type CitySample } from './snapshot'
const props = defineProps<{ query: string; province: string; grade: string; order: string; page: number }>()
const emit = defineEmits<{ filter: [changes: Record<string, string | undefined>]; detail: [city: CitySample] }>()
const matches = computed(() => filteredCities(props.query, props.province, props.grade, props.order))
const size = 5
const count = computed(() => Math.max(1, Math.ceil(matches.value.length / size)))
const current = computed(() => Math.min(props.page, count.value))
const rows = computed(() => matches.value.slice((current.value - 1) * size, current.value * size))
const heading = ref<HTMLHeadingElement>()
function focus() { heading.value?.focus({ preventScroll: true }); heading.value?.scrollIntoView({ block: 'start' }) }
defineExpose({ focus })
const filters = computed(() => [
  { key: 'province', label: '省份筛选', value: props.province, options: [{ label: '全部省份', value: '' }, ...provinces.map(p => ({ label: p, value: p }))] },
  { key: 'grade', label: 'AQI 等级筛选', value: props.grade, options: [{ label: '全部 AQI 等级', value: '' }, ...grades.map((label, i) => ({ label, value: String(i + 1) }))] },
  { key: 'order', label: '排序', value: props.order === 'asc' ? 'asc' : '', options: [{ label: 'AQI 降序', value: '' }, { label: 'AQI 升序', value: 'asc' }] },
])
function filter(key: string, value: string) { emit('filter', { [key]: value || undefined, page: undefined }) }
function clear() { emit('filter', { q: undefined, province: undefined, grade: undefined, order: undefined, page: undefined }) }
</script>
<template>
  <section class="sky-glass sky-table" aria-labelledby="city-list-heading">
    <div class="table-toolbar"><div class="table-title"><h2 id="city-list-heading" ref="heading" tabindex="-1">城市空气质量列表</h2><span class="pill-count">{{ cities.length }} 城</span></div>
      <div class="table-filters">
        <label class="sky-input"><Search aria-hidden="true"/><input type="search" aria-label="搜索城市" name="city-search" autocomplete="off" placeholder="搜索城市…" :value="query" @input="filter('q', ($event.target as HTMLInputElement).value)"/></label>
        <ElSelect v-for="item in filters" :key="item.key" class="sky-select" :class="`select-${item.key}`" :name="`city-${item.key}`" :aria-label="item.label" :model-value="item.value"
          :empty-values="[null, undefined]" popper-class="sky-options" :show-arrow="false" :offset="6" placement="bottom-start"
          :fallback-placements="['top-start']" @update:model-value="filter(item.key, $event)">
          <template v-if="item.key === 'order'" #prefix><span class="sort-prefix">排序</span></template>
          <ElOption v-for="option in item.options" :key="option.value" :value="option.value" :label="option.label">
            <span>{{ option.label }}</span><Check v-if="item.value === option.value" aria-hidden="true"/>
          </ElOption>
        </ElSelect>
        <button class="sky-icon clear-filters" title="重置列表筛选和排序" aria-label="重置列表筛选和排序" @click="clear"><RefreshLeft aria-hidden="true"/></button>
      </div>
    </div>
    <div class="sky-table-scroll" role="region" aria-label="城市观测数据，可横向滚动" tabindex="0"><table>
      <thead><tr><th scope="col">同刻排名</th><th scope="col">城市</th><th scope="col">所属省份</th><th scope="col" :aria-sort="order === 'asc' ? 'ascending' : 'descending'"><button class="sort-head" aria-label="切换 AQI 排序" @click="filter('order', order === 'asc' ? '' : 'asc')">AQI <component :is="order === 'asc' ? SortUp : SortDown" aria-hidden="true"/></button></th><th scope="col">空气质量等级</th><th scope="col">首要污染物</th><th scope="col">PM₂.₅ 等级</th><th scope="col">O₃ 等级</th><th scope="col">操作</th></tr></thead>
      <tbody><tr v-for="city in rows" :key="city.name"><td class="num-muted">{{ String(city.rank).padStart(2, '0') }}</td><th scope="row">{{ city.name }}</th><td>{{ city.province }}</td><td><b class="aqi-num" :class="`g${city.grade}`">{{ city.aqi }}</b></td><td><span class="grade-label" :class="`g${city.grade}`">{{ grades[city.grade - 1] }}</span></td><td>{{ city.primary }}</td><td>{{ city.levels.PM25 }} 级</td><td>{{ city.levels.O3 === null ? '缺测' : `${city.levels.O3} 级` }}</td><td><button class="sky-text-link" :aria-label="`查看${city.name}详情`" @click="emit('detail', city)">详情<ArrowRight aria-hidden="true"/></button></td></tr></tbody>
    </table></div>
    <div v-if="!rows.length" class="sky-empty"><Search aria-hidden="true"/><b>没有匹配的城市</b><button class="sky-text-link" @click="clear">清除筛选</button></div>
    <div class="table-footer"><span role="status" aria-live="polite">共 {{ matches.length }} 个结果<span v-if="matches.length"> · 显示 {{ (current - 1) * size + 1 }}–{{ Math.min(current * size, matches.length) }}</span></span><nav aria-label="城市列表分页"><button class="sky-icon" aria-label="上一页" :disabled="current === 1" @click="emit('filter', { page: String(current - 1) })"><ArrowLeft aria-hidden="true"/></button><span>{{ current }} / {{ count }}</span><button class="sky-icon" aria-label="下一页" :disabled="current === count" @click="emit('filter', { page: String(current + 1) })"><ArrowRight aria-hidden="true"/></button></nav></div>
  </section>
</template>
