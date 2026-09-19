import { defineStore } from 'pinia'
import { getDocList, getDocTree, updateDoc, uploadDoc } from '@/api/doc'

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    // 文档列表
    docs: [],
    // 当前选中文档ID
    currentDocId: null,
    // 文档树数据
    treeData: [],
    // 当前选中章节/段落
    currentNode: null,
    // 加载状态
    loadingDocs: false,
    loadingTree: false,
    uploading: false,
  }),

  getters: {
    // 按状态过滤
    docStatusMap: () => ({
      active: { label: '生效', type: 'success' },
      draft: { label: '草案', type: 'warning' },
      invalid: { label: '失效', type: 'info' },
    }),
  },

  actions: {
    // 加载文档列表
    async loadDocs() {
      this.loadingDocs = true
      try {
        const res = await getDocList()
        const data = res.data || res || []
        this.docs = Array.isArray(data) ? data : data.items || []
        return this.docs
      } catch (e) {
        console.error('加载文档列表失败', e)
        return []
      } finally {
        this.loadingDocs = false
      }
    },

    // 加载目录树
    async loadTree(docId) {
      if (!docId) {
        this.treeData = []
        return []
      }
      this.currentDocId = docId
      this.loadingTree = true
      try {
        const res = await getDocTree(docId)
        const data = res.data || res || []
        // 后端返回 { chapter_list: [...] }，章节节点带 paragraphs 而非 children
        const chapterList = Array.isArray(data) ? data : Array.isArray(data.chapter_list) ? data.chapter_list : []
        this.treeData = this._formatTree(chapterList)
        return this.treeData
      } catch (e) {
        console.error('加载目录树失败', e)
        return []
      } finally {
        this.loadingTree = false
      }
    },

    // 格式化树节点（递归添加key等信息）
    _formatTree(nodes) {
      return (nodes || []).map((node) => ({
        ...node,
        key: node.id,
        label: node.title || node.name || node.chapter_title,
        children: this._formatTree(node.children || node.paragraphs),
      }))
    },

    // 上传文档
    async upload(file) {
      this.uploading = true
      try {
        const formData = new FormData()
        formData.append('file', file)
        const res = await uploadDoc(formData)
        await this.loadDocs()
        return res
      } catch (e) {
        console.error('上传文档失败', e)
        throw e
      } finally {
        this.uploading = false
      }
    },

    // 局部更新文档
    async updateNode(payload) {
      try {
        await updateDoc(payload)
        return true
      } catch (e) {
        console.error('更新文档失败', e)
        return false
      }
    },

    // 选中节点
    selectNode(node) {
      this.currentNode = node
    },
  },
})
