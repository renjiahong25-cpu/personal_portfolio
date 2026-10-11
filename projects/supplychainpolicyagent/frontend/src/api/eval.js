import request from './request'

/**
 * 模型指标
 */
export function getMetrics() {
  return request.get('/eval/metric')
}

/**
 * BadCase列表
 * @param {object} params { page, page_size, keyword, status }
 */
export function getBadCases(params) {
  return request.get('/eval/badcase', { params })
}

/**
 * 触发评测
 * @param {object} data
 */
export function runEval(data) {
  return request.post('/eval/run', data)
}
