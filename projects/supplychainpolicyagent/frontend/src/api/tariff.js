import request from './request'

/**
 * 商品描述检索 HS 编码候选
 * @param {object} params { q, country, limit }
 */
export function searchTariff(params) {
  return request.get('/tariff/search', { params })
}

/**
 * 商品描述 → HS 编码 + 出关/入关双侧税率面板
 * @param {object} data { description, origin_country, dest_country, top_k }
 */
export function predictTariff(data) {
  return request.post('/tariff/predict', data)
}

/**
 * HS 编码详情（父级链 + 指定方向税率）
 * @param {object} params { hs_code, country, direction }
 */
export function getTariffDetail(params) {
  return request.get('/tariff/detail', { params })
}

/**
 * 查询某 HS 编码指定方向税率明细
 * @param {object} params { hs_code, country, direction }
 */
export function getTariffDuties(params) {
  return request.get('/tariff/duties', { params })
}

/**
 * 手动更新/强制刷新某编码 出关+入关 双侧税率
 * @param {object} data { hs_code, origin_country, dest_country, force }
 */
export function fetchTariff(data) {
  return request.post('/tariff/fetch', data)
}

/**
 * 数据概况与最近更新/抓取日志
 */
export function getTariffStats(params) {
  return request.get('/tariff/stats', { params })
}

/**
 * 手动触发全量文件导入（可选离线能力）
 * @param {object} data { source, country }
 */
export function syncTariff(data) {
  return request.post('/tariff/sync', data)
}

/**
 * 人工修正：手填某编码某一方向税率落库（抓取不会覆盖）
 * @param {object} data { hs_code, country, direction, rows }
 */
export function saveManualRates(data) {
  return request.post('/tariff/manual', data)
}

/**
 * 对美报价成本重算（综合关税 / 路径对比 / HS 自查 / 出口效益）
 * @param {object} data { description, value_usd, mode, incoterm, origin_country, dest_country, top_k, with_llm }
 */
export function quoteEstimate(data) {
  return request.post('/tariff/quote', data)
}

/**
 * 异步排队报价任务状态查询（快同步/慢异步混合模式：status=queued/processing/done/failed）
 * @param {string} jobId
 */
export function getQuoteJob(jobId) {
  return request.get(`/tariff/quote/jobs/${jobId}`)
}

/**
 * 报价引擎版本与政策清单快照
 */
export function getQuoteVersions() {
  return request.get('/tariff/quote/versions')
}

/**
 * 任意两国进口关税查询（A→B 工具，world_mfn 快照 + DutyRate 基础 MFN）
 * @param {object} params { origin, dest, hs }
 */
export function getWorldAb(params) {
  return request.get('/tariff/ab', { params })
}

/**
 * world_mfn 静态快照元信息（支持国家 + 覆盖条数 + as_of）
 */
export function getWorldSources() {
  return request.get('/tariff/world-sources')
}

/**
 * 多中转路径报价：CN→{VN/MX/TH/SG/MY}*→US（route 省略=预置路径对比）
 * @param {object} data { description, value_usd, route?, top_k, with_llm, hs_code? }
 */
export function getQuoteRoute(data) {
  return request.post('/tariff/quote/route', data)
}

/**
 * 商品名模糊搜索：泛化词 → HS 候选（关键词/同义词典快路径 + LLM 兜底，候选经官方税则校验）
 * @param {object} data { q, dest, limit }
 */
export function smartSearch(data) {
  return request.post('/tariff/smart-search', data, { timeout: 180000 })
}