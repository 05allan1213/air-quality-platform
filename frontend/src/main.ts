import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import '@fontsource-variable/inter/index.css'
import '@fontsource-variable/noto-sans-sc/index.css'
import App from './App.vue'
import { router } from './router'
import './styles/theme.css'

const app = createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn })
void router.isReady().then(() => app.mount('#app'))
