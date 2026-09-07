<template>
  <div class="knowledge-view">
    <!-- 工具栏 -->
    <div class="toolbar panel-card">
      <div class="toolbar-left">
        <el-button type="primary" @click="$refs.uploadInput.click()">
          <el-icon class="el-icon--left"><Upload /></el-icon>
          上传文档
        </el-button>
        <input
          ref="uploadInput"
          type="file"
          accept=".pdf,.docx,.md,.txt"
          style="display: none"
          @change="handleUpload"
        />
        <el-button @click="loadAll">
          <el-icon class="el-icon--left"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
      <div class="toolbar-right">
        <el-input
          v-model="searchKey"
          placeholder="搜索文档"
          clearable
          style="width: 220px"
          :prefix-icon="'Search'"
          @input="filteredDocs"
        />
      </div>
    </div>

    <div class="knowledge-body">
      <!-- 文档列表 -->
      <div class="doc-panel panel-card">
        <div class="card-title">文档列表</div>
        <div v-loading="knowledge.loadingDocs" class="doc-list">
          <EmptyState
            v-if="!filteredDocs.length && !knowledge.loadingDocs"
            title="暂无文档"
            description="点击上方按钮上传知识文档，或通过爬虫采集规则资料"
          />
          <el-table
            v-else
            :data="filteredDocs"
            :height="tableHeight"
            highlight-current-row
            @current-change="handleDocSelect"
          >
            <el-table-column prop="title" label="文档名称" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="doc-name">
                  <el-icon color="#409EFF"><Document /></el-icon>
                  <span>{{ row.title || row.name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusMap[row.status]?.type || 'info'" size="small" effect="light">
                  {{ statusMap[row.status]?.label || row.status || '未知' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="doc_type" label="类型" width="90" align="center">
              <template #default="{ row }">
                <el-tag
                  :type="row.doc_type === 'pdf' ? 'danger' : row.doc_type === 'text' ? 'warning' : 'primary'"
                  size="small"
                  effect="plain"
                >
                  {{ (row.doc_type || 'text').toUpperCase() }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="170">
              <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="160" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click.stop="viewTree(row)">目录</el-button>
                <el-button link type="warning" size="small" @click.stop="reSlice(row)">重新切片</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <!-- 目录树 -->
      <div class="tree-panel panel-card">
        <div class="card-title">章节目录</div>
        <div v-loading="knowledge.loadingTree" class="tree-content">
          <EmptyState
            v-if="!treeData.length && !knowledge.loadingTree"
            title="选择文档查看目录"
            description="从左侧选择一篇文档，查看其章节结构"
            :image-size="60"
          />
          <el-tree
            v-else
            :data="treeData"
            :props="treeProps"
            node-key="id"
            highlight-current
            default-expand-all
            @node-click="handleNodeClick"
          >
            <template #default="{ data }">
              <div class="tree-node">
                <el-icon v-if="data.children && data.children.length" color="#409EFF"><FolderOpened /></el-icon>
                <el-icon v-else color="#909399"><Document /></el-icon>
                <span class="tree-node-label">{{ data.label }}</span>
                <span v-if="data.page" class="tree-page">P{{ data.page }}</span>
              </div>
            </template>
          </el-tree>
        </div>
      </div>

      <!-- 段落详情 -->
      <div class="detail-panel panel-card">
        <div class="card-title">段落详情</div>
        <div class="detail-content">
          <EmptyState
            v-if="!currentNode"
            title="查看段落内容"
            description="点击右侧目录树中的节点，查看对应段落详情"
            :image-size="60"
          />
          <template v-else>
            <div class="detail-header">
              <el-tag size="small" type="primary">{{ currentNode.label }}</el-tag>
              <span v-if="currentNode.page" class="detail-page">第 {{ currentNode.page }} 页</span>
            </div>
            <div class="detail-text">{{ currentNode.content || currentNode.text || '该节点暂无详细内容' }}</div>
            <div class="detail-actions">
              <el-button type="primary" size="small" @click="openEdit">
                <el-icon class="el-icon--left"><Edit /></el-icon>
                编辑更新
              </el-button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialog.visible" title="局部更新段落" width="600px">
      <el-form :model="editDialog" label-width="80px">
        <el-form-item label="章节">
          <el-input :model-value="currentNodeLabel" disabled />
        </el-form-item>
        <el-form-item label="内容">
          <el-input
            v-model="editDialog.content"
            type="textarea"
            :rows="8"
            placeholder="请输入更新后的段落内容"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { ElMessage, ElMessageBox } from 'element-plus'
import EmptyState from '@/components/EmptyState.vue'

const knowledge = useKnowledgeStore()
const searchKey = ref('')
const uploadInput = ref(null)
const currentDoc = ref(null)
const tableHeight = ref(520)
const saving = ref(false)

const statusMap = {
  active: { label: '生效', type: 'success' },
  draft: { label: '草案', type: 'warning' },
  invalid: { label: '失效', type: 'info' },
  pending: { label: '待处理', type: 'primary' },
  failed: { label: '异常', type: 'danger' },
}

const treeProps = {
  children: 'children',
  label: 'label',
}

const treeData = computed(() => knowledge.treeData)

const currentNode = computed(() => knowledge.currentNode)

const currentNodeLabel = computed(() => currentNode.value?.label || '')

// 搜索过滤
const filteredDocs = computed(() => {
  const docs = knowledge.docs
  if (!searchKey.value) return docs
  const key = searchKey.value.toLowerCase()
  return docs.filter((d) => (d.title || d.name || '').toLowerCase().includes(key))
})

// 上传
const handleUpload = async (e) => {
  const file = e.target.files[0]
  if (!file) return
  try {
    await knowledge.upload(file)
    ElMessage.success('文档上传成功')
  } catch (err) {
    ElMessage.error('文档上传失败')
  } finally {
    e.target.value = ''
  }
}

// 选择文档
const handleDocSelect = (row) => {
  if (row) {
    currentDoc.value = row
    viewTree(row)
  }
}

// 查看目录
const viewTree = (row) => {
  currentDoc.value = row
  knowledge.loadTree(row.id || row.doc_id)
}

// 重新切片
const reSlice = (row) => {
  ElMessageBox.confirm(
    `确认对文档「${row.title || row.name}」执行重新切片吗？`,
    '重新切片',
    { type: 'warning' }
  ).then(async () => {
    try {
      await knowledge.updateNode({ doc_id: row.id || row.doc_id, action: 'reslice' })
      ElMessage.success('已触发重新切片')
    } catch (e) {
      ElMessage.error('操作失败')
    }
  }).catch(() => {})
}

// 节点点击
const handleNodeClick = (data) => {
  knowledge.selectNode(data)
}

// 打开编辑
const openEdit = () => {
  editDialog.value.content = currentNode.value?.content || currentNode.value?.text || ''
  editDialog.value.visible = true
}

// 保存编辑
const saveEdit = async () => {
  if (!currentNode.value) return
  saving.value = true
  try {
    await knowledge.updateNode({
      doc_id: knowledge.currentDocId,
      node_id: currentNode.value.id,
      content: editDialog.value.content,
      title: currentNode.value.label,
    })
    ElMessage.success('更新成功')
    editDialog.value.visible = false
    knowledge.loadTree(knowledge.currentDocId)
  } catch (e) {
    ElMessage.error('更新失败')
  } finally {
    saving.value = false
  }
}

// 刷新所有
const loadAll = () => {
  knowledge.loadDocs()
  if (currentDoc.value) {
    knowledge.loadTree(currentDoc.value.id || currentDoc.value.doc_id)
  }
}

// 时间格式化
const formatDateTime = (ts) => {
  if (!ts) return ''
  const date = new Date(ts)
  if (isNaN(date)) return ts
  const pad = (n) => (n < 10 ? '0' + n : n)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const editDialog = ref({
  visible: false,
  content: '',
})

onMounted(() => {
  knowledge.loadDocs()
})
</script>

<style scoped>
.knowledge-view {
  height: 100%;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding: 16px 20px;
}

.toolbar-left {
  display: flex;
  gap: 8px;
}

.knowledge-body {
  display: grid;
  grid-template-columns: 3fr 2fr 2fr;
  gap: 16px;
  height: calc(100vh - 180px);
}

.doc-panel,
.tree-panel,
.detail-panel {
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.doc-list {
  flex: 1;
  overflow: auto;
}

.doc-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tree-content,
.detail-content {
  flex: 1;
  overflow: auto;
}

.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.tree-node-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-page {
  font-size: 11px;
  color: #909399;
  margin-left: auto;
}

.detail-content {
  padding: 4px;
}

.detail-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.detail-page {
  font-size: 12px;
  color: #909399;
}

.detail-text {
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
  background: #f7f9fc;
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
}

.detail-actions {
  margin-top: 16px;
}

@media (max-width: 1400px) {
  .knowledge-body {
    grid-template-columns: 1fr 1fr;
  }
  .detail-panel {
    grid-column: span 2;
  }
}
</style>
