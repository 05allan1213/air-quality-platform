<script setup lang="ts">
import { computed } from 'vue'
import { ElDrawer, ElTooltip, type ElTooltipProps } from 'element-plus'
import { CollectionTag, DArrowLeft, DArrowRight, DataAnalysis, Grid, InfoFilled, Location, Search, Switch } from '@element-plus/icons-vue'
import skyMark from './assets/sky-mark.svg'

const props = defineProps<{ mobileOpen: boolean }>()
const expanded = defineModel<boolean>('expanded', { required: true })
const emit = defineEmits<{ close: []; workspace: []; scope: [name: string]; info: [] }>()
const drawer = computed({ get: () => props.mobileOpen, set: (value) => { if (!value) emit('close') } })
const groups = [
  { title: '观测工作区', links: [{ label: '全国总览', icon: Grid, path: '/' }, { label: '城市详情', icon: Location, path: '/city' }, { label: '分析工作台', icon: DataAnalysis, path: '/analysis' }] },
  { title: '数据与个人', links: [{ label: '数据查询', icon: Search, path: '' }, { label: '我的空间', icon: CollectionTag, path: '' }] },
]
const toggleLabel = computed(() => expanded.value ? '收起侧栏' : '展开侧栏')
const tools = [
  { label: '数据说明', icon: InfoFilled, run: () => emit('info') },
  { label: '切换工作区', icon: Switch, run: () => emit('workspace') },
]
// Teleported hints remain visible when the middle section has to scroll.
const hint = { placement: 'right', trigger: ['hover', 'focus'], triggerKeys: [], showAfter: 180, hideAfter: 0, showArrow: false, popperClass: 'sky-rail-tip' } satisfies Partial<ElTooltipProps>
</script>
<template>
  <aside class="sky-side" :class="{ compact: !expanded }" aria-label="侧栏">
    <div class="side-atmosphere" aria-hidden="true" />
    <ElTooltip v-bind="hint" :disabled="expanded" content="澄空观测">
      <RouterLink to="/" class="sky-brand rail-item" aria-label="澄空观测，返回全国总览">
        <img :src="skyMark" width="36" height="36" alt="" />
        <span><strong>澄空观测</strong><small>空气质量监测平台</small></span>
      </RouterLink>
    </ElTooltip>
    <nav id="national-rail-nav" class="sky-nav" aria-label="主导航">
      <div v-for="group in groups" :key="group.title">
        <p>{{ group.title }}</p>
        <ElTooltip v-for="item in group.links" :key="item.label" v-bind="hint" :disabled="expanded" :content="item.label">
          <RouterLink v-if="item.path" :to="item.path" class="rail-item" :aria-label="item.label" :class="{ 'sky-active': item.path === '/' }">
            <component :is="item.icon" aria-hidden="true" /><span>{{ item.label }}</span>
          </RouterLink>
          <button v-else class="rail-item" :aria-label="item.label" @click="emit('scope', item.label)">
            <component :is="item.icon" aria-hidden="true" /><span>{{ item.label }}</span>
          </button>
        </ElTooltip>
      </div>
    </nav>
    <div class="side-bottom">
      <ElTooltip v-for="tool in tools" :key="tool.label" v-bind="hint" :disabled="expanded" :content="tool.label">
        <button class="rail-item" :aria-label="tool.label" @click="tool.run"><component :is="tool.icon" aria-hidden="true" /><span>{{ tool.label }}</span></button>
      </ElTooltip>
      <div class="side-divider" aria-hidden="true" />
      <button class="rail-item rail-toggle" :aria-label="toggleLabel" aria-controls="national-rail-nav" :aria-expanded="expanded" @click="expanded = !expanded">
        <component :is="expanded ? DArrowLeft : DArrowRight" aria-hidden="true" /><span>{{ toggleLabel }}</span>
      </button>
    </div>
  </aside>
  <ElDrawer v-model="drawer" title="工作区导航" direction="ltr" size="min(305px, 85vw)" class="sky-mobile-drawer" destroy-on-close>
    <nav class="sky-nav" aria-label="窄屏主导航">
      <div v-for="group in groups" :key="group.title">
        <p>{{ group.title }}</p>
        <template v-for="item in group.links" :key="item.label">
          <RouterLink v-if="item.path" :to="item.path" class="rail-item" :class="{ 'sky-active': item.path === '/' }" @click="emit('close')"><component :is="item.icon" aria-hidden="true" />{{ item.label }}</RouterLink>
          <button v-else class="rail-item" @click="emit('close'); emit('scope', item.label)"><component :is="item.icon" aria-hidden="true" />{{ item.label }}</button>
        </template>
      </div>
    </nav>
    <button class="rail-item drawer-workspace" @click="emit('close'); emit('workspace')"><Switch aria-hidden="true" />切换工作区</button>
  </ElDrawer>
</template>
