import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

const app = createApp(App)

/**
 * A render error in one component used to take the whole tree down silently:
 * the page stayed visible but every button stopped working. Surface it instead.
 */
app.config.errorHandler = (error, _instance, info) => {
  console.error('[PianoFlow] unhandled error:', error, info)
  if (document.querySelector('.fatal-banner')) return
  const banner = document.createElement('div')
  banner.className = 'fatal-banner'
  banner.textContent = `界面出错了：${error?.message || error}（详情见浏览器控制台）`
  document.body.appendChild(banner)
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('[PianoFlow] unhandled rejection:', event.reason)
})

app.mount('#app')
