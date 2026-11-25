<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'
import { Odometer } from '@element-plus/icons-vue'

const router = useRouter()
const session = useSessionStore()
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await session.register(username.value, password.value)
    await router.replace({ name: 'login' })
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '账号未能创建，请检查输入后重试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <RouterLink class="brand" :to="{ name: 'login' }"><span class="brand-mark"><el-icon><Odometer /></el-icon></span><span class="brand-name">城市空气质量平台</span></RouterLink>
    <section class="auth-panel panel">
      <h1>创建普通账号</h1>
      <p class="auth-intro">新账号仅获得普通用户能力。管理权限需由具名授权流程授予。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label for="username">用户名</label>
        <input id="username" v-model="username" autocomplete="username" required maxlength="150" />
        <label for="password">密码</label>
        <input id="password" v-model="password" type="password" autocomplete="new-password" required maxlength="1024" />
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="btn primary auth-submit" type="submit" :disabled="busy">{{ busy ? '正在创建…' : '创建账号' }}</button>
      </form>
      <p class="auth-foot">已有账号？ <RouterLink :to="{ name: 'login' }">返回登录</RouterLink></p>
    </section>
  </main>
</template>
