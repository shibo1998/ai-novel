// 入口：pinia + router + element-plus 暗色主题与命令式组件样式（规格04 §0）。
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'

import 'element-plus/theme-chalk/dark/css-vars.css'
// ElMessage / ElMessageBox 为命令式调用，unplugin 按需导入覆盖不到，手动补样式
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import './styles/tailwind.css'
import './styles/main.scss'

createApp(App).use(createPinia()).use(router).mount('#app')
