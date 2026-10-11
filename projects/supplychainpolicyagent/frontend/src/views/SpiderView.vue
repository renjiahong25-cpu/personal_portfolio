<template>
  <div class="spider-view">
    <!-- 顶部操作栏 -->
    <div class="toolbar panel-card">
      <div class="toolbar-left">
        <el-button type="primary" @click="openAddDialog">
          <el-icon class="el-icon--left"><Plus /></el-icon>
          新增站点
        </el-button>
        <el-button @click="loadSites">
          <el-icon class="el-icon--left"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
      <div class="toolbar-right">
        <el-tag type="success" effect="plain">运行中站点: {{ runCount }}</el-tag>
        <el-tag type="danger" effect="plain" style="margin-left: 8px">异常站点: {{ errorCount }}</el-tag>
      </div>
    </div>

    <div class="spider-body">
      <!-- 站点列表 -->
      <div class="site-panel panel-card">
        <div class="card-title">站点列表</div>
        <div v-loading="loadingSites" class="site-list">
          <EmptyState
            v-if="!sites.length && !loadingSites"
            title="暂无站点"
            description="添加爬虫站点，采集跨境清关规则资料"
          />
          <div v-for="site in sites" :key="site.id" class="site-card" @click="selectSite(site)">
            <div class="site-card-header">
              <div class="site-info">
                <el-icon color="#409EFF"><Link /></el-icon>
                <div class="site-name">
                  <span class="site-title">{{ site.name }}</span>
                  <span class="site-url">{{ site.url }}</span>
                </div>
              </div>
              <el-tag :type="siteStatusMap[site.status]?.type || 'info'" size="small" effect="dark">
                {{ siteStatusMap[site.status]?.label || site.status || '未知' }}
              </el-tag>
            </div>
            <div class="site-meta">
              <span>类型: {{ site.site_type || site.type || '-' }}</span>
              <span>文档数: {{ site.doc_count || 0 }}</span>
              <span>更新时间: {{ formatDateTime(site.updated_at) }}</span>
            </div>
            <div class="site-actions" v-if="selectedSiteId === site.id">
              <el-button size="small" type="primary" plain @click.stop="triggerFull(site)">
                <el-icon class="el-icon--left"><VideoPlay /></el-icon>
                冷启动触发
              </el-button>
              <el-button size="small" type="success" plain @click.stop="triggerIncremental(site)">
                <el-icon class="el-icon--left"><Update /></el-icon>
                增量抓取
              </el-button>
              <el-button size="small" link type="primary" @click.stop="showLog(site)">日志</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：配置编辑 & 任务进度 -->
      <div class="config-panel panel-card">
        <div class="card-title">站点配置</div>
        <div class="config-content">
          <EmptyState
            v-if="!selectedSite"
            title="选择站点查看配置"
            description="点击左侧站点卡片查看其爬虫配置"
            :image-size="60"
          />
          <template v-if="selectedSite">
            <el-form label-width="80px" class="config-form">
              <el-form-item label="站点名称">
                <el-input :model-value="selectedSite.name" disabled />
              </el-form-item>
              <el-form-item label="抓取规则">
                <!-- YAML配置高亮编辑器 -->
                <div class="yaml-editor">
                  <div class="yaml-line-numbers">
                    <div v-for="n in yamlLines.length" :key="n" class="line-num">{{ n }}</div>
                  </div>
                  <textarea
                    v-model="yamlContent"
                    class="yaml-textarea"
                    spellcheck="false"
                    @input="recalculateYamlLines"
                  ></textarea>
                </div>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="saveConfig">保存配置</el-button>
                <el-button @click="resetConfig">重置</el-button>
              </el-form-item>
            </el-form>

            <!-- 任务进度 -->
            <div class="task-section">
              <div class="task-title">最近任务</div>
              <div v-if="!tasks.length" class="task-empty">
                <span>暂无任务记录</span>
              </div>
              <div v-for="task in tasks" :key="task.id" class="task-item">
                <div class="task-header">
                  <span class="task-mode">{{ task.mode === 'full' ? '冷启动' : '增量' }}</span>
                  <el-tag :type="taskStatus(task.status)" size="small">{{ task.status }}</el-tag>
                  <span class="task-time">{{ formatDateTime(task.created_at) }}</span>
                </div>
                <el-progress
                  :percentage="task.progress || 0"
                  :status="task.progress >= 100 ? 'success' : undefined"
                  :stroke-width="6"
                />
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 新增站点对话框 -->
    <el-dialog v-model="addDialog.visible" title="新增爬虫站点" width="520px">
      <el-form :model="addForm" label-width="80px">
        <el-form-item label="站点名称" required>
          <el-input v-model="addForm.name" placeholder="如：德国海关官网" />
        </el-form-item>
        <el-form-item label="站点URL" required>
          <el-input v-model="addForm.url" placeholder="https://..." />
        </el-form-item>
        <el-form-item label="站点类型">
          <el-select v-model="addForm.site_type" placeholder="选择类型">
            <el-option label="政府官网" value="gov" />
            <el-option label="行业协会" value="association" />
            <el-option label="电商平台" value="ecommerce" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="采集说明">
          <el-input
            v-model="addForm.description"
            type="textarea"
            :rows="3"
            placeholder="描述该站点的抓取规则说明"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="submitAdd">确认添加</el-button>
      </template>
    </el-dialog>

    <!-- 日志对话框 -->
    <el-dialog v-model="logDialog.visible" title="任务日志" width="640px">
      <div class="log-container">
        <div v-for="(line, i) in logLines" :key="i" class="log-line">
          <span class="log-time">{{ line.time }}</span>
          <span class="log-level" :class="line.level">{{ line.level }}</span>
          <span class="log-msg">{{ line.msg }}</span>
        </div>
        <EmptyState v-if="!logLines.length" title="暂无日志" :image-size="50" />
      </div>
      <template #footer>
        <el-button @click="logDialog.visible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { addSite, runSpider } from '@/api/spider'
import { ElMessage } from 'element-plus'
import EmptyState from '@/components/EmptyState.vue'

const sites = ref([])
const loadingSites = ref(false)
const selectedSiteId = ref(null)
const selectedSite = computed(() => sites.value.find((s) => s.id === selectedSiteId.value) || null)
const yamlContent = ref('')
const yamlLines = ref([])
const tasks = ref([])
const adding = ref(false)

const siteStatusMap = {
  running: { label: '运行中', type: 'success' },
  paused: { label: '暂停', type: 'warning' },
  error: { label: '异常', type: 'danger' },
  pending: { label: '待审核', type: 'primary' },
  idle: { label: '闲置', type: 'info' },
}

const runCount = computed(() => sites.value.filter((s) => s.status === 'running').length)
const errorCount = computed(() => sites.value.filter((s) => s.status === 'error' || s.status === 'pending').length)

const addDialog = ref({ visible: false })
const addForm = ref({
  name: '',
  url: '',
  site_type: 'gov',
  description: '',
})

const logDialog = ref({ visible: false })
const logLines = ref([])

// 加载站点（示例数据填充）
const loadSites = async () => {
  loadingSites.value = true
  try {
    // 实际根据后端API接入
    // const res = await getSites()
    // sites.value = res.data || []
    // 示例数据
    sites.value = sampleSites()
  } catch (e) {
    console.error('加载站点失败', e)
    ElMessage.error('加载站点失败')
  } finally {
    loadingSites.value = false
  }
}

// 示例站点数据
const sampleSites = () => {
  return [
    {
      id: 1,
      name: '德国海关总署',
      url: 'https://www.zoll.de',
      status: 'running',
      site_type: 'gov',
      doc_count: 128,
      updated_at: '2026-09-05T10:30:00',
      config: 'name: 德国海关总署\nurl: https://www.zoll.de\n' +
        'allow_domains:\n  - zoll.de\nrules:\n  - name: 清关公告\n' +
        '    selectors:\n      title: h1\n      content: article\n',
    },
    {
      id: 2,
      name: '欧盟委员会贸易',
      url: 'https://trade.ec.europa.eu',
      status: 'pending',
      site_type: 'gov',
      doc_count: 0,
      updated_at: '2026-09-06T08:00:00',
      config: 'name: 欧盟委员会贸易\nurl: https://trade.ec.europa.eu\nrules:\n  - name: 规则文章\n    selectors:\n      title: h1\n',
    },
    {
      id: 3,
      name: '德国联邦经济部',
      url: 'https://www.bmwi.de',
      status: 'error',
      site_type: 'gov',
      doc_count: 45,
      updated_at: '2026-09-04T14:20:00',
      config: 'name: 德国联邦经济部\nurl: https://www.bmwi.de\nrules:\n  - name: 经贸政策\n    selectors:\n      title: h1\n',
    },
  ]
}

// 选择站点
const selectSite = (site) => {
  selectedSiteId.value = site.id
  yamlContent.value = site.config || '# 暂无配置'
  recalculateYamlLines()
  loadTasks(site)
}

// YAML行号
const recalculateYamlLines = () => {
  yamlLines.value = yamlContent.value.split('\n')
}

// 加载任务
const loadTasks = (site) => {
  tasks.value = [
    {
      id: 1,
      mode: 'full',
      status: 'completed',
      progress: 100,
      created_at: '2026-09-05T10:30:00',
    },
    {
      id: 2,
      mode: 'incremental',
      status: 'running',
      progress: 60,
      created_at: '2026-09-06T09:00:00',
    },
  ]
}

const taskStatus = (status) => {
  const map = { completed: 'success', running: 'primary', failed: 'danger', pending: 'warning' }
  return map[status] || 'info'
}

// 冷启动
const triggerFull = (site) => {
  ElMessage.success(`已触发「${site.name}」冷启动抓取`)
  runSpider({ site_id: site.id, mode: 'full' }).catch(() => {})
}

// 增量
const triggerIncremental = (site) => {
  ElMessage.success(`已触发「${site.name}」增量抓取`)
  runSpider({ site_id: site.id, mode: 'incremental' }).catch(() => {})
}

// 保存配置
const saveConfig = () => {
  ElMessage.success('配置已保存')
}

const resetConfig = () => {
  if (selectedSite.value) {
    yamlContent.value = selectedSite.value.config || '# 暂无配置'
    recalculateYamlLines()
  }
}

// 新增对话框
const openAddDialog = () => {
  addForm.value = { name: '', url: '', site_type: 'gov', description: '' }
  addDialog.value.visible = true
}

const submitAdd = async () => {
  if (!addForm.value.name || !addForm.value.url) {
    ElMessage.warning('请填写站点名称和URL')
    return
  }
  adding.value = true
  try {
    await addSite(addForm.value)
    ElMessage.success('站点添加成功')
    addDialog.value.visible = false
    loadSites()
  } catch (e) {
    ElMessage.error('添加失败')
  } finally {
    adding.value = false
  }
}

// 日志
const showLog = (site) => {
  logLines.value = [
    { time: '2026-09-06 09:00:01', level: 'info', msg: `开始抓取站点: ${site.name}` },
    { time: '2026-09-06 09:00:05', level: 'info', msg: '已获取首页机器人协议 robots.txt' },
    { time: '2026-09-06 09:00:12', level: 'success', msg: '解析列表页, 发现 12 个链接' },
    { time: '2026-09-06 09:01:30', level: 'warning', msg: '跳过重复文档: 20260812-verordnung' },
    { time: '2026-09-06 09:02:15', level: 'info', msg: '文档下载完成, 开始切片' },
    { time: '2026-09-06 09:03:00', level: 'success', msg: '切片完成: 128 个段落' },
  ]
  logDialog.value.visible = true
}

const formatDateTime = (ts) => {
  if (!ts) return ''
  const date = new Date(ts)
  if (isNaN(date)) return ts
  const pad = (n) => (n < 10 ? '0' + n : n)
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(() => {
  loadSites()
})
</script>

<style scoped>
.spider-view {
  height: 100%;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding: 16px 20px;
}

.spider-body {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  height: calc(100vh - 180px);
}

.site-panel,
.config-panel {
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.site-list {
  flex: 1;
  overflow-y: auto;
}

.site-card {
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.site-card:hover {
  border-color: #409EFF;
  box-shadow: 0 2px 12px rgba(64, 158, 255, 0.12);
  transform: translateY(-1px);
}

.site-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.site-info {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.site-name {
  display: flex;
  flex-direction: column;
}

.site-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}

.site-url {
  font-size: 12px;
  color: #909399;
}

.site-meta {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}

.site-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
}

.config-content {
  flex: 1;
  overflow-y: auto;
}

/* YAML编辑器 */
.yaml-editor {
  display: flex;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  overflow: hidden;
  height: 240px;
  background: #1e1e1e;
}

.yaml-line-numbers {
  background: #252526;
  color: #858585;
  padding: 8px 6px;
  text-align: right;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  flex-shrink: 0;
  width: 36px;
  overflow: hidden;
}

.line-num {
  height: 19.2px;
}

.yaml-textarea {
  flex: 1;
  background: #1e1e1e;
  color: #d4d4d4;
  border: none;
  outline: none;
  padding: 8px 12px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  resize: none;
}

/* 任务 */
.task-section {
  margin-top: 20px;
}

.task-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 12px;
}

.task-item {
  background: #fafbfc;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}

.task-mode {
  font-weight: 600;
  color: #409EFF;
}

.task-time {
  font-size: 12px;
  color: #909399;
  margin-left: auto;
}

.task-empty {
  color: #909399;
  font-size: 13px;
  text-align: center;
  padding: 20px;
}

/* 日志 */
.log-container {
  height: 400px;
  overflow-y: auto;
  background: #1e1e1e;
  border-radius: 6px;
  padding: 12px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12px;
}

.log-line {
  display: flex;
  gap: 8px;
  padding: 2px 0;
}

.log-time {
  color: #858585;
}

.log-level {
  width: 42px;
  color: #d4d4d4;
}

.log-level.info { color: #569cd6; }
.log-level.success { color: #6a9955; }
.log-level.warning { color: #ce9178; }
.log-level.error { color: #f48771; }

.log-msg {
  color: #d4d4d4;
}
</style>
