<script setup lang="ts">
defineProps<{ busy: boolean; error?: string; empty: boolean; emptyText: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <p v-if="busy" class="session-state" role="status">正在读取记录…</p>
  <div v-else-if="error" class="me-actions">
    <p class="status status-error" role="alert">{{ error }}</p>
    <button class="btn" type="button" @click="$emit('retry')">重新读取</button>
  </div>
  <p v-else-if="empty" class="me-empty">{{ emptyText }}</p>
</template>
