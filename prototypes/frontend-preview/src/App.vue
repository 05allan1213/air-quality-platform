<script setup lang="ts">
import { ref } from 'vue'
import { ElConfigProvider, ElDialog } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

const settings = ref(false)
const scopeLabel = ref('')
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <a class="skip-link" href="#content">跳到主要内容</a>
    <RouterView @settings="settings = true" @scope="scopeLabel = $event" />
    <ElDialog v-model="settings" title="演示设置" width="min(460px, calc(100% - 32px))" align-center destroy-on-close>
      <p class="dialog-intro">全部样本来自本地 F 合成空间。「治理账号」为演示标签。</p>
      <template #footer><button class="btn" @click="settings = false">完成设置</button></template>
    </ElDialog>
    <ElDialog :model-value="Boolean(scopeLabel)" :title="`${scopeLabel} · 原型范围外`" width="min(440px, calc(100% - 32px))" align-center @update:model-value="scopeLabel = ''">
      <p class="dialog-intro">当前原型开放全国总览。</p>
    </ElDialog>
  </ElConfigProvider>
</template>
