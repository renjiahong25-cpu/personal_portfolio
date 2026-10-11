import request from './request'

/**
 * 文档上传
 * @param {FormData} formData 包含文件
 */
export function uploadDoc(formData) {
  return request.post('/doc/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/**
 * 文档列表
 */
export function getDocList(params) {
  return request.get('/doc/list', { params })
}

/**
 * 文档目录树
 * @param {string} docId 文档ID
 */
export function getDocTree(docId) {
  return request.get('/doc/tree', { params: { doc_id: docId } })
}

/**
 * 局部更新文档
 * @param {object} data
 */
export function updateDoc(data) {
  return request.post('/doc/update', data)
}
