<template>
  <main class="public-page">
    <RouterLink class="brand" :to="{ name: 'login' }"><span class="brand-mark"><el-icon><Odometer /></el-icon></span><span class="brand-name">城市空气质量平台</span></RouterLink>
    <article class="public-copy">
      <h1>城市空气质量监测数据分析平台</h1>
      <p>面向城市与监测站点的空气质量观察、研究分析和数据治理工作。</p>
      <p>平台按数据产品、来源、对象、时间窗口、标准和使用授权分别呈现观测。全国名录、城市观测、站点观测与趋势数据不会互相替代。</p>
      <RouterLink class="btn primary" :to="{ name: 'login' }">进入平台</RouterLink>
    </article>
    <footer class="auth-footer"><RouterLink :to="{ name: 'sources' }">数据与来源</RouterLink></footer>
  </main>
</template>

<script setup lang="ts">
import { Odometer } from '@element-plus/icons-vue'
</script>
