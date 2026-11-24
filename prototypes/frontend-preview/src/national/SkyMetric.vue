<script setup lang="ts">
import type { Component } from 'vue'
defineProps<{ label: string; value: string | number; unit?: string; note: string; icon: Component; fresh?: boolean }>()
</script>
<template>
  <div class="sky-glass sky-kpi" :class="{ 'kpi-fresh': fresh }">
    <div class="kpi-heading"><span class="kpi-lens"><component :is="icon" aria-hidden="true"/></span><span>{{ label }}</span></div>
    <strong class="kpi-reading">{{ value }}<small v-if="unit">{{ unit }}</small></strong>
    <span class="kpi-note">{{ note }}</span>
  </div>
</template>
