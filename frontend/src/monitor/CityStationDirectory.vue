<script setup lang="ts">
import { watch } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import { localApiHref, type PageInfo, useMonitorPages } from './reads'

const props = defineProps<{ href: string; product: number }>()
defineEmits<{ open: [href: string] }>()

type Epoch = { station: number; site_epoch: number; station_name: string | null; site_name: string | null; codes: string[]; status: string; link: string }
const directory = useMonitorPages<{ results: Epoch[]; page: PageInfo }>()

watch(() => props.href, href => {
  const target = localApiHref(href)
  if (target) directory.open(target)
  else directory.clear()
}, { immediate: true })
</script>

<template>
  <div class="directory">
    <h3>站点产品 {{ product }}</h3>
    <p v-if="directory.busy.value" role="status">正在读取站点目录…</p>
    <p v-else-if="directory.error.value" role="alert">{{ directory.error.value.message }}</p>
    <template v-else-if="directory.data.value">
      <ul v-if="directory.data.value.results.length">
        <li v-for="epoch in directory.data.value.results" :key="epoch.site_epoch">
          <button type="button" class="btn object-link" @click="$emit('open', epoch.link)">
            <span>{{ epoch.site_name ?? epoch.station_name ?? `站点 ${epoch.station}` }}</span>
            <small>{{ epoch.status === 'historical' ? '历史站址期' : epoch.status === 'current' ? '当前站址期' : '状态未知' }} · {{ epoch.codes.length ? epoch.codes.join('、') : '代码未披露' }}</small>
            <ArrowRight aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-else>当前时刻没有可披露的站址期。</p>
    </template>
    <div v-if="directory.hasCursor.value || directory.data.value?.page.next_cursor || directory.error.value" class="pager">
      <button v-if="directory.hasCursor.value || directory.error.value" class="btn" type="button" :disabled="directory.busy.value" @click="directory.first">{{ directory.hasCursor.value ? '站点目录第一页' : '重新读取站点目录' }}</button>
      <button v-if="directory.data.value?.page.next_cursor" class="btn" type="button" @click="directory.next">下一页站点</button>
    </div>
  </div>
</template>

<style scoped>
.directory { min-width: 0; margin-top: 14px; }
h3 { margin: 0 0 8px; font-size: 14px; }
p { margin: 8px 0; font-size: 13px; }
ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.object-link { display: grid; grid-template-columns: minmax(0, 1fr) auto; width: 100%; padding: 9px 12px; gap: 3px 8px; text-align: start; }
.object-link small { grid-column: 1; color: var(--aq-secondary); font-weight: 400; overflow-wrap: anywhere; }
.object-link svg { grid-area: 1 / 2 / span 2 / 3; }
.pager { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
</style>
