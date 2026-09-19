import axios from 'axios'
import { ElMessage } from 'element-plus'

const service = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

// 请求拦截器：添加日志
service.interceptors.request.use(
  (config) => {
    // 开发环境打印请求日志
    if (import.meta.env.DEV) {
      console.log('[API 请求]', config.method.toUpperCase(), config.url, config.params || '', config.data || '')
    }
    return config
  },
  (error) => {
    console.error('[API 请求错误]', error)
    return Promise.reject(error)
  }
)

// 响应拦截器：统一错误处理
service.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) {
      console.log('[API 响应]', response.config.url, response.data)
    }
    return response.data
  },
  (error) => {
    console.error('[API 响应错误]', error)
    const config = error.config
    // 超时重试逻辑（最多重试1次）
    if (error.code === 'ECONNABORTED' && config && !config._retried) {
      config._retried = true
      return service(config)
    }
    let message = '网络请求失败，请稍后重试'
    if (error.response) {
      const status = error.response.status
      const detail = error.response.data && error.response.data.detail
      // 后端 detail 优先（如"未匹配到 HS 编码"），通用文案仅作兜底
      if (status === 401) message = detail || '未授权，请重新登录'
      else if (detail) message = detail
      else if (status === 500) message = '服务器内部错误'
      else if (status === 404) message = '请求的资源不存在'
      else message = `请求失败（${status}）`
    } else if (error.code === 'ECONNABORTED') {
      message = '请求超时，请稍后重试'
    }
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service
