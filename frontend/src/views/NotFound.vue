<template>
  <section class="panel not-found">
    <h1>未找到此页面</h1>
    <p>地址可能已变化，或页面不在当前工作区。</p>
    <RouterLink class="btn primary" :to="{ name: 'overview' }">返回监测总览</RouterLink>
  </section>
</template>
