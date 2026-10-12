import request from './request'

/**
 * 站点列表
 * GET /api/spider/site/list → { code, msg, data: [{id, site_name, site_url, yaml_config, status, ...}] }
 * @param {number} [status] 按状态过滤（1运行/2暂停/3异常/4待审核）
 */
export function getSites(status) {
  const params = status != null ? { status } : undefined
  return request.get('/spider/site/list', { params })
}

/**
 * 新增爬虫站点
 * @param {object} data { site_name, site_url, yaml_config }
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
