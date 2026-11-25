<template>
  <main class="public-page">
    <RouterLink class="brand" :to="{ name: 'login' }"><span class="brand-mark"><el-icon><Odometer /></el-icon></span><span class="brand-name">城市空气质量平台</span></RouterLink>
    <article class="public-copy">
      <h1>数据来源与使用范围</h1>
      <p>可见数据由服务端按当前会话、对象、数据产品和用途授权后返回。页面不会推断或扩展来源许可。</p>
      <dl class="source-list">
        <div><dt>L · 全国城市名录</dt><dd>同一来源观测时刻下的城市名录与汇总。</dd></div>
        <div><dt>C · 城市观测</dt><dd>城市级空气质量观测；城市七项指标来自此产品。</dd></div>
        <div><dt>T · AQI 趋势</dt><dd>单独的近期 AQI 序列，与城市小时观测保持区分。</dd></div>
        <div><dt>S · 站点观测</dt><dd>按站点及站址期识别的观测。</dd></div>
        <div><dt>D / H · 报告与历史</dt><dd>分别依照对应验证和当前有效授权呈现。</dd></div>
        <div><dt>F · 合成验证数据</dt><dd>仅用于隔离环境中的软件链验证，不代表真实城市发布。</dd></div>
      </dl>
      <p class="status">地图及未获得当前授权的数据可能不可用。数据空缺不会以其他产品的读数补齐。</p>
      <RouterLink class="btn primary" :to="{ name: 'login' }">返回登录</RouterLink>
    </article>
  </main>
</template>

<script setup lang="ts">
import { Odometer } from '@element-plus/icons-vue'
</script>
