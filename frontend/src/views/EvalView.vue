<template>
  <div class="eval-view">
    <!-- 指标仪表盘 -->
    <div class="metric-panel panel-card">
      <div class="panel-header">
        <div class="card-title">评测指标</div>
        <el-button type="primary" size="small" @click="triggerEval">
          <el-icon class="el-icon--left"><VideoPlay /></el-icon>
          触发评测
        </el-button>
      </div>

      <div v-loading="loadingMetrics" class="metric-grid">
        <div v-for="metric in metrics" :key="metric.key" class="metric-card" :class="metricKeyClass(metric.key)">
          <div class="metric-icon">
            <el-icon :size="26"><component :is="metric.icon" /></el-icon>
          </div>
          <div class="metric-info">
            <div class="metric-value">{{ formatPercent(metric.value) }}</div>
            <div class="metric-label">{{ metric.label }}</div>
          </div>
          <div class="metric-trend" v-if="metric.trend">
            <span :class="metric.trend > 0 ? 'up' : 'down'">
              {{ metric.trend > 0 ? '↑' : '↓' }} {{ Math.abs(metric.trend) }}%
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- BadCase列表 -->
    <div class="badcase-panel panel-card">
      <div class="card-title">BadCase 列表</div>

      <!-- 筛选器 -->
      <div class="filter-bar">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索问题关键词"
          clearable
          style="width: 220px"
          :prefix-icon="'Search'"
          @clear="loadBadCases"
          @input="debouncedSearch"
        />
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 140px" @change="loadBadCases">
          <el-option label="待处理" value="open" />
          <el-option label="已修复" value="fixed" />
          <el-option label="复现中" value="reproducing" />
        </el-select>
        <el-select v-model="filters.category" placeholder="分类" clearable style="width: 140px" @change="loadBadCases">
          <el-option label="回答错误" value="wrong_answer" />
          <el-option label="知识缺失" value="missing_knowledge" />
          <el-option label="溯源错误" value="wrong_source" />
        </el-select>
      </div>

      <div v-loading="loadingCases" class="badcase-list">
        <EmptyState
          v-if="!badCases.length && !loadingCases"
          title="暂无BadCase"
          description="目前没有需要处理的问题案例，表现很棒！"
        />
        <el-table v-else :data="badCases" stripe>
          <el-table-column prop="question" label="问题" min-width="200" show-overflow-tooltip />
          <el-table-column prop="category" label="分类" width="110" align="center">
            <template #default="{ row }">
              <el-tag :type="categoryMap[row.category]?.type || 'info'" size="small" effect="plain">
                {{ categoryMap[row.category]?.label || row.category }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="statusMap[row.status]?.type || 'info'" size="small">
                {{ statusMap[row.status]?.label || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="错误答案" prop="error_answer" min-width="200" show-overflow-tooltip />
          <el-table-column label="期望/备注" prop="note" min-width="160" show-overflow-tooltip />
          <el-table-column prop="reported_at" label="反馈时间" width="160">
            <template #default="{ row }">{{ formatDateTime(row.reported_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="viewBadCase(row)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 分页 -->
        <div class="pagination">
          <el-pagination
            v-model:current-page="filters.page"
            v-model:page-size="filters.page_size"
            :total="total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="loadBadCases"
            @current-change="loadBadCases"
          />
        </div>
      </div>
    </div>

    <!-- BadCase详情对话框 -->
    <el-dialog v-model="detailDialog.visible" title="BadCase 详情" width="620px">
      <div class="case-detail">
        <div class="detail-row">
          <span class="detail-label">问题</span>
          <div class="detail-value">{{ detailDialog.data.question }}</div>
        </div>
        <div class="detail-row">
          <span class="detail-label">错误答案</span>
          <div class="detail-value wrong">{{ detailDialog.data.error_answer }}</div>
        </div>
        <div class="detail-row">
          <span class="detail-label">期望输出</span>
          <div class="detail-value correct">{{ detailDialog.data.expected_answer }}</div>
        </div>
        <div class="detail-row" v-if="detailDialog.data.sources">
          <span class="detail-label">溯源</span>
          <div class="detail-value">{{ detailDialog.data.sources }}</div>
        </div>
        <div class="detail-row">
          <span class="detail-label">反馈备注</span>
          <div class="detail-value">{{ detailDialog.data.note || '-' }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="detailDialog.visible = false">关闭</el-button>
        <el-button type="primary" @click="markFixed">标记已修复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getMetrics, getBadCases, runEval } from '@/api/eval'
import { ElMessage, ElMessageBox } from 'element-plus'
import EmptyState from '@/components/EmptyState.vue'

const loadingMetrics = ref(false)
const loadingCases = ref(false)
const metrics = ref([])
const badCases = ref([])
const total = ref(0)

const filters = reactive({
  keyword: '',
  status: '',
  category: '',
  page: 1,
  page_size: 10,
})

const statusMap = {
  open: { label: '待处理', type: 'danger' },
  fixed: { label: '已修复', type: 'success' },
  reproducing: { label: '复现中', type: 'warning' },
}

const categoryMap = {
  wrong_answer: { label: '回答错误', type: 'danger' },
  missing_knowledge: { label: '知识缺失', type: 'warning' },
  wrong_source: { label: '溯源错误', type: 'primary' },
}

const detailDialog = reactive({
  visible: false,
  data: {},
})

// 加载指标
const loadMetrics = async () => {
  loadingMetrics.value = true
  try {
    // 实际接入后端API
    // const res = await getMetrics()
    // metrics.value = res.data || []
    metrics.value = [
      { key: 'accuracy', label: '回答准确率', value: 92.5, icon: 'CircleCheck', trend: 2.3 },
      { key: 'coverage', label: '知识覆盖率', value: 85.0, icon: 'Histogram', trend: 1.8 },
      { key: 'recall', label: '溯源召回率', value: 88.2, icon: 'Search', trend: -0.5 },
      { key: 'latency', label: '平均响应时间(s)', value: 1.8, icon: 'Timer', trend: 12 },
    ]
  } catch (e) {
    console.error('加载指标失败', e)
    ElMessage.error('加载指标失败')
    metrics.value = []
  } finally {
    loadingMetrics.value = false
  }
}

// 加载BadCase
const loadBadCases = async () => {
  loadingCases.value = true
  try {
    // 实际接入后端API
    // const res = await getBadCases(filters)
    // badCases.value = res.data || []
    // total.value = res.total || 0
    badCases.value = sampleBadCases()
    total.value = sampleBadCases().length
  } catch (e) {
    console.error('加载BadCase失败', e)
    ElMessage.error('加载BadCase失败')
    badCases.value = []
  } finally {
    loadingCases.value = false
  }
}

const sampleBadCases = () => {
  return [
    {
      id: 1,
      question: '出口欧盟的纺织品需要哪些标签？',
      category: 'missing_knowledge',
      status: 'open',
      error_answer: '未能提供具体的标签要求',
      expected_answer: '需提供成分标签、origin标识、CE标志等',
      note: '知识库缺少纺织品章节',
      reported_at: '2026-09-05T10:20:00',
    },
    {
      id: 2,
      question: '锂电池空运到德国的包装要求？',
      category: 'wrong_answer',
      status: 'reproducing',
      error_answer: '错误引用了海运包装标准',
      expected_answer: '航空运输需符合IATA危险品规则',
      note: '引用了错误章节',
      reported_at: '2026-09-04T16:45:00',
    },
    {
      id: 3,
      question: 'CE认证和EAC认证的区别？',
      category: 'wrong_source',
      status: 'fixed',
      error_answer: '笼统回答缺乏区分',
      expected_answer: '区分欧盟CE与欧亚EAC认证体系',
      note: '已修复并更新知识库',
      reported_at: '2026-09-03T09:30:00',
    },
  ]
}

// 触发评测
const triggerEval = async () => {
  ElMessageBox.confirm(
    '确认触发一次完整评测吗？评测过程可能需要几分钟。',
    '触发评测',
    { type: 'warning', confirmButtonText: '开始评测' }
  ).then(async () => {
    try {
      await runEval({ version: 'v1.0.0' })
      ElMessage.success('评测已触发，请稍后查看结果')
    } catch (e) {
      ElMessage.error('评测触发失败')
    }
  }).catch(() => {})
}

// 查看详情
const viewBadCase = (row) => {
  detailDialog.data = { ...row }
  detailDialog.visible = true
}

const markFixed = () => {
  ElMessage.success('已标记为已修复')
  detailDialog.visible = false
}

// 指标样式
const metricKeyClass = (key) => {
  const map = {
    accuracy: 'metric-accuracy',
    coverage: 'metric-coverage',
    recall: 'metric-recall',
    latency: 'metric-latency',
  }
  return map[key] || ''
}

// 百分比格式化
const formatPercent = (value) => {
  if (typeof value === 'number' && value <= 1 && value > 0.01 && Number.isInteger(value * 100)) {
    return (value * 100).toFixed(1) + '%'
  }
  if (typeof value === 'number') return value + (value > 20 ? '%' : '')
  return value
}

// 防抖搜索
let searchTimer = null
const debouncedSearch = () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadBadCases(), 300)
}

const formatDateTime = (ts) => {
  const date = new Date(ts)
  if (isNaN(date)) return ts
  const pad = (n) => (n < 10 ? '0' + n : n)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(() => {
  loadMetrics()
  loadBadCases()
})
</script>

<style scoped>
.eval-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 8px;
}

.metric-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  padding: 16px;
  transition: all 0.2s;
}

.metric-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

.metric-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

.metric-accuracy .metric-icon { background: #67C23A; }
.metric-coverage .metric-icon { background: #409EFF; }
.metric-recall .metric-icon { background: #E6A23C; }
.metric-latency .metric-icon { background: #F56C6C; }

.metric-value {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}

.metric-label {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
}

.metric-trend {
  margin-left: auto;
  font-size: 12px;
}

.metric-trend .up {
  color: #67C23A;
}

.metric-trend .down {
  color: #F56C6C;
}

/* BadCase */
.badcase-panel {
  padding-bottom: 12px;
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 详情 */
.detail-row {
  margin-bottom: 16px;
}

.detail-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.detail-value {
  background: #fafbfc;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 14px;
  line-height: 1.6;
}

.detail-value.wrong {
  background: #fef0f0;
  color: #F56C6C;
}

.detail-value.correct {
  background: #f0f9eb;
  color: #67C23A;
}

@media (max-width: 1200px) {
  .metric-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
