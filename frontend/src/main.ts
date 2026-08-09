import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { initTelegram } from '@/shared/telegram/webapp'
import './styles/base.css'

// Инициализация Telegram WebApp (ready/expand/тема) до маунта.
// Вне Telegram включается dev-мок темы и initData из VITE_DEV_INIT_DATA.
initTelegram()

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
