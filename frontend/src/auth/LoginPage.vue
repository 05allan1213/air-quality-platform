<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'
import { Odometer } from '@element-plus/icons-vue'

const route = useRoute()
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
    await session.signIn(username.value, password.value)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') && !route.query.next.startsWith('//') ? route.query.next : '/'
    await router.replace(next)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '登录未完成，请稍后重试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <RouterLink class="brand" :to="{ name: 'login' }"><span class="brand-mark"><el-icon><Odometer /></el-icon></span><span class="brand-name">城市空气质量平台</span></RouterLink>
    <section class="auth-panel panel">
      <h1>登录</h1>
      <p class="auth-intro">使用平台账号继续访问获授权的监测与研究数据。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label for="username">用户名</label>
        <input id="username" v-model="username" autocomplete="username" required maxlength="150" />
        <label for="password">密码</label>
        <input id="password" v-model="password" type="password" autocomplete="current-password" required maxlength="1024" />
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="btn primary auth-submit" type="submit" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
      </form>
      <p class="auth-foot">还没有账号？ <RouterLink :to="{ name: 'register' }">创建普通账号</RouterLink></p>
    </section>
    <footer class="auth-footer"><RouterLink :to="{ name: 'about' }">平台说明</RouterLink><RouterLink :to="{ name: 'sources' }">数据与来源</RouterLink></footer>
  </main>
</template>
