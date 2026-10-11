import request from './request'

/**
 * 新增爬虫站点
 * @param {object} data
 */
export function addSite(data) {
  return request.post('/spider/site/add', data)
}

/**
 * 触发抓取
 * @param {object} data { site_id, mode: 'full' | 'incremental' }
 */
export function runSpider(data) {
  return request.post('/spider/run', data)
}

/**
 * 候选配置
 */
export function getCandidates() {
  return request.get('/spider/candidate')
}

/**
 * 审核配置
 * @param {object} data
 */
export function auditConfig(data) {
  return request.post('/spider/audit', data)
}
