<template>
  <div class="tariff-page">
    <!-- 顶部：智能分类（出关→入关 双栏） -->
    <div class="panel-card">
      <div class="card-title">HS 编码智能分类（出关 → 入关）</div>
      <div class="predict-row">
        <span class="field-label">出口国</span>
        <el-select v-model="originCountry" style="width: 110px">
          <el-option label="中国" value="CN" />
          <el-option label="德国" value="DE" />
          <el-option label="美国" value="US" />
        </el-select>
        <span class="arrow">→</span>
        <span class="field-label">进口国</span>
        <el-select v-model="destCountry" style="width: 110px">
          <el-option label="德国" value="DE" />
          <el-option label="欧盟" value="EU" />
          <el-option label="中国" value="CN" />
          <el-option label="美国" value="US" />
        </el-select>
        <el-input
          v-model="description"
          placeholder="输入商品描述，如：26寸电动自行车 / LED 户外投光灯 / 木制办公桌"
          clearable
          @keyup.enter="doPredict"
        />
        <el-button type="primary" :loading="predicting" @click="doPredict">智能分类</el-button>
      </div>

      <template v-if="result">
        <div class="result-head">
          <el-tag v-if="result.used_llm" type="warning" effect="light">LLM 裁决</el-tag>
          <el-tag v-else type="success" effect="light">离线匹配</el-tag>
          <span v-if="result.reason" class="result-reason">{{ result.reason }}</span>
          <span class="route">线路：{{ result.origin_country }} 出关 → {{ result.dest_country }} 入关</span>
        </div>
        <div v-if="result.chosen" class="chosen-card">
          <div class="chosen-code">{{ result.chosen.hs_code }}</div>
          <div class="chosen-desc">
            {{ result.chosen.description_cn || result.chosen.description_en }}
          </div>
          <el-tag size="small" type="info">匹配度 {{ (result.chosen.score * 100).toFixed(1) }}%</el-tag>
          <el-tag v-if="result.chosen.level" size="small" effect="plain">第 {{ result.chosen.level }} 位细目</el-tag>
          <el-button
            link
            type="primary"
            :loading="refreshing"
            @click="doFetchFresh"
            style="margin-left: auto"
          >重新抓取税率</el-button>
        </div>

        <div class="dual-panel">
          <!-- 出关面板 -->
          <div class="side-panel">
            <div class="panel-title">
              <el-icon color="#E6A23C"><Van /></el-icon>
              出关侧 · {{ result.origin_country }} 出口关税 / 退税
              <el-tag v-if="exportPanel" size="small" :type="exportStatusTag" effect="plain" style="margin-left:auto">
                {{ exportStatusText }}
              </el-tag>
            </div>
            <el-empty v-if="!exportPanel || !exportPanel.rows || !exportPanel.rows.length" :image-size="50"
                      :description="exportEmptyText" />
            <editable-table v-else :rows="exportPanel.rows" :hs-code="result.chosen.hs_code"
                            country="CN" direction="export" />
          </div>

          <!-- 入关面板 -->
          <div class="side-panel">
            <div class="panel-title">
              <el-icon color="#67C23A"><Ship /></el-icon>
              入关侧 · {{ result.dest_country }} 进口关税 / 增值税
              <el-tag v-if="importPanel" size="small" :type="importStatusTag" effect="plain" style="margin-left:auto">
                {{ importStatusText }}
              </el-tag>
            </div>
            <el-empty v-if="!importPanel || !importPanel.rows || !importPanel.rows.length" :image-size="50"
                      :description="importEmptyText" />
            <editable-table v-else :rows="importPanel.rows" :hs-code="result.chosen.hs_code"
                            country="DE" direction="import" />
          </div>
        </div>
        <div class="disclaimer">税率经在线抓取后本地缓存，仅供参考；实际以报关时海关/税务机关当日数据为准。</div>
      </template>
    </div>

    <!-- 中部：HS 检索 -->
    <div class="panel-card">
      <div class="card-title">HS 编码检索</div>
      <div class="predict-row">
        <el-select v-model="searchCountry" style="width: 130px" placeholder="国家">
          <el-option label="美国 US" value="US" />
          <el-option label="欧盟 EU" value="EU" />
          <el-option label="中国 CN" value="CN" />
        </el-select>
        <el-input v-model="searchQ" placeholder="输入关键词检索候选编码" clearable @keyup.enter="doSearch" />
        <el-button type="primary" plain :loading="searching" @click="doSearch">检索</el-button>
      </div>

      <el-table v-if="searchResults.length" :data="searchResults" size="small" border style="margin-top: 12px">
        <el-table-column prop="hs_code" label="HS 编码" width="120" />
        <el-table-column label="品目描述" min-width="260">
          <template #default="{ row }">{{ row.description_cn || row.description_en }}</template>
        </el-table-column>
        <el-table-column label="位数" width="70">
          <template #default="{ row }">{{ row.level }}</template>
        </el-table-column>
        <el-table-column label="匹配度" width="90">
          <template #default="{ row }">{{ (row.score * 100).toFixed(1) }}%</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.hs_code)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="searched" description="未检索到候选，可尝试切换国家或扩大描述" />
    </div>

    <!-- 中转国：任意两国 A→B 进口关税速查 -->
    <div class="panel-card">
      <div class="card-title">
        任意两国进口关税速查（A → B）
        <el-tooltip content="world_mfn 官方税则快照：越南/新加坡/马来西亚/泰国/墨西哥；美/欧/中/德为基础 MFN" placement="top">
          <el-icon color="#909399" style="margin-left: 6px"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="worldMeta.as_of" size="small" effect="plain" style="margin-left: auto">
          数据版本 {{ worldMeta.as_of }} · {{ worldMeta.countryCounts || worldMeta.countries?.length || 0 }} 国
        </el-tag>
      </div>
      <div class="predict-row">
        <span class="field-label">出口国</span>
        <el-select v-model="abOrigin" style="width: 120px">
          <el-option v-for="c in abCountries" :key="c.code" :label="c.code + ' ' + c.label" :value="c.code" />
        </el-select>
        <span class="arrow">→</span>
        <span class="field-label">进口国</span>
        <el-select v-model="abDest" style="width: 150px">
          <el-option v-for="c in abCountries" :key="c.code" :label="c.code + ' ' + c.label" :value="c.code" />
        </el-select>
        <el-input
          v-model="abHs"
          placeholder="输入 HS 编码或商品名（如 61091010 / 摩托车 / 人偶）"
          clearable
          style="width: 300px"
          @keyup.enter="doWorldAb"
        />
        <el-button
          type="primary"
          :loading="abLoading"
          :loading-text="abIsName ? '智能匹配中…' : '查询中…'"
          @click="doWorldAb"
        >查询税率</el-button>
        <el-tooltip content="支持泛化商品名：多个匹配时列出候选供选择；仅 1 个匹配时自动视为精准" placement="top">
          <el-icon color="#909399"><InfoFilled /></el-icon>
        </el-tooltip>
      </div>

      <template v-if="abCandidates">
        <div v-if="abCandidates.candidates.length" class="ab-cand-wrap">
          <div class="ab-cand-head">
            <span class="ab-cand-title">{{ abCandidates.candidates.length }} 个匹配商品</span>
            <el-tag v-if="abCandidates.via === 'llm'" size="small" type="warning" effect="light">LLM 智能匹配</el-tag>
            <el-tag v-else size="small" type="success" effect="light">关键词匹配</el-tag>
            <span class="ab-cand-hint">{{ abCandidates.candidates.length === 1 ? '精准匹配' : '点击行查看该商品税率' }}</span>
          </div>
          <el-table :data="abCandidates.candidates" size="small" border
                    :row-class-name="() => 'ab-cand-clickable'" @row-click="onCandPick">
            <el-table-column prop="hs" label="HS 编码" width="130" />
            <el-table-column label="品名" min-width="240">
              <template #default="{ row }">{{ row.name_cn || row.name_en || '--' }}</template>
            </el-table-column>
            <el-table-column prop="name_en" label="英文名" min-width="180">
              <template #default="{ row }">{{ row.name_en || '--' }}</template>
            </el-table-column>
            <el-table-column label="进口税率" width="110">
              <template #default="{ row }">{{ row.rate != null ? row.rate + '%' : '--' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button link type="primary" @click.stop="onCandPick(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <el-empty v-else description="未找到匹配商品，可尝试更具体的名称或改用 HS 编码" :image-size="50" />
      </template>

      <template v-if="abResult">
        <div class="ab-result">
          <div class="ab-row">
            <span class="ab-provenance">{{ abResult.from || abOrigin }} → {{ abResult.to || abDest }} · 进口</span>
            <span class="ab-hs">HS {{ abResult.hs }}</span>
            <span class="ab-source">{{ abResult.source }}</span>
            <span v-if="abResult.as_of" class="ab-asof">截至 {{ abResult.as_of }}</span>
          </div>
          <div class="ab-rate-line">
            <span class="ab-rate" :class="{ 'zero': abResult.rate === 0 }">
              {{ abResult.rate != null ? abResult.rate + '%' : '暂无税率' }}
            </span>
            <span v-if="abResult.rate_text" class="ab-rate-text">{{ abResult.rate_text }}</span>
          </div>
          <div class="ab-note">{{ abResult.note }}</div>
          <div v-if="abResult.desc" class="ab-note">品目：{{ abResult.desc }}</div>
          <el-table v-if="abResult.sub_rates && Object.keys(abResult.sub_rates).length" :data="subRateRows" size="small"
                    border style="margin-top: 8px">
            <el-table-column prop="hs" label="子行 HS" width="120" />
            <el-table-column label="税率">
              <template #default="{ row }">{{ row.rate != null ? row.rate + '%' : '--' }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div class="disclaimer">中转国税率 = 官方税则静态快照（不含 301/对等附加）；实际以报关当日海关税则为准。</div>
      </template>
    </div>

    <!-- 底部：数据概况 -->
    <div class="panel-card">
      <div class="card-title">税率数据概况</div>
      <div class="stats-row">
        <div class="stat-item">
          <div class="stat-num">{{ stats.total_codes || 0 }}</div>
          <div class="stat-label">HS 分类词典</div>
        </div>
        <div class="stat-item">
          <div class="stat-num">{{ stats.total_rates || 0 }}</div>
          <div class="stat-label">税率条数(按需缓存)</div>
        </div>
        <div class="stat-item">
          <div class="stat-num">{{ stats.countries || 0 }}</div>
          <div class="stat-label">覆盖国家</div>
        </div>
        <div class="stat-item">
          <div class="stat-num stat-small">{{ stats.last_fetch ? stats.last_fetch.create_time : (stats.updated_at || '--') }}</div>
          <div class="stat-label" v-if="stats.last_fetch">
            最近抓取 #{{ stats.last_fetch.hs_code }} [{{ stats.last_fetch.source }}] {{ stats.last_fetch.status }}
          </div>
          <div class="stat-label" v-else>最近更新</div>
        </div>
      </div>
      <div v-if="worldMeta.counts" class="world-meta">
        <div class="card-subtitle" style="margin: 12px 0 8px">world_mfn 官方税则快照覆盖</div>
        <div class="world-counts">
          <div v-for="(cnt, c) in worldMeta.counts" :key="c" class="world-count-item">
            <b>{{ countryLabel(c) }} ({{ c }})</b>：{{ cnt }} 条
          </div>
        </div>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="HS 编码详情" size="440px">
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="编码">{{ detail.hs_code }}</el-descriptions-item>
          <el-descriptions-item label="国家">{{ detail.country }}</el-descriptions-item>
          <el-descriptions-item label="位数">{{ detail.level }}</el-descriptions-item>
          <el-descriptions-item label="数据源">{{ detail.source }}</el-descriptions-item>
          <el-descriptions-item label="英文描述">{{ detail.description_en }}</el-descriptions-item>
          <el-descriptions-item label="单位">{{ detail.unit_en || detail.unit_cn }}</el-descriptions-item>
        </el-descriptions>
        <div class="card-subtitle" style="margin-top: 16px">父级链路</div>
        <el-timeline>
          <el-timeline-item v-for="a in detail.ancestors" :key="a.hs_code" :timestamp="a.hs_code">
            {{ a.description_cn || a.description_en }}
          </el-timeline-item>
        </el-timeline>
        <div class="card-subtitle" style="margin-top: 16px">适用税率（全部方向）</div>
        <el-table :data="detail.duties" size="small" border>
          <el-table-column prop="direction" label="方向" width="70" />
          <el-table-column prop="duty_type_label" label="税种" width="110" />
          <el-table-column prop="duty_rate" label="税率" />
          <el-table-column prop="trade_partner" label="范围" />
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, defineAsyncComponent } from 'vue'
import { ElMessage } from 'element-plus'
import {
  predictTariff,
  searchTariff,
  getTariffDetail,
  getTariffStats,
  fetchTariff,
  getWorldAb,
  getWorldSources,
  smartSearch,
} from '@/api/tariff'
import { WORLD_COUNTRIES, countryLabel } from '@/constants/world'

const EditableTable = defineAsyncComponent(() => import('@/components/TariffRateTable.vue'))

const originCountry = ref('CN')
const destCountry = ref('DE')
const description = ref('')
const predicting = ref(false)
const result = ref(null)
const refreshing = ref(false)

const searchCountry = ref('US')
const searchQ = ref('')
const searching = ref(false)
const searchResults = ref([])
const searched = ref(false)

const stats = ref({})
const worldMeta = ref({})
const abCountries = WORLD_COUNTRIES
const abOrigin = ref('CN')
const abDest = ref('VN')
const abHs = ref('')
const abLoading = ref(false)
const abResult = ref(null)
const abCandidates = ref(null)
const abIsName = computed(() => {
  const v = abHs.value.replace(/\s+/g, '')
  return v !== '' && !/^\d{6,10}$/.test(v)
})

const subRateRows = computed(() => {
  const sr = abResult.value?.sub_rates
  if (!sr) return []
  return Object.entries(sr).map(([hs, rate]) => ({ hs, rate }))
})

const detailVisible = ref(false)
const detail = ref(null)

const exportPanel = computed(() => (result.value && result.value.export_panel) || null)
const importPanel = computed(() => (result.value && result.value.import_panel) || null)

const exportStatusTag = computed(() => (exportPanel.value && exportPanel.value.status === 'failed') ? 'danger' : 'success')
const importStatusTag = computed(() => (importPanel.value && importPanel.value.status === 'failed') ? 'danger' : 'success')
const exportStatusText = computed(() => {
  if (!exportPanel.value) return ''
  return exportPanel.value.status === 'failed' ? '抓取失败，可手动填写' : (exportPanel.value.status === 'cached' ? '缓存' : '已抓取')
})
const importStatusText = computed(() => {
  if (!importPanel.value) return ''
  return importPanel.value.status === 'failed' ? '抓取失败，可手动填写' : (importPanel.value.status === 'cached' ? '缓存' : '已抓取')
})
const exportEmptyText = computed(() => {
  if (!exportPanel.value) return '未抓取出关数据'
  return exportPanel.value.status === 'failed' ? (exportPanel.value.message || '输出关抓取失败') : '出关侧暂无税率'
})
const importEmptyText = computed(() => {
  if (!importPanel.value) return '未抓取入关数据'
  return importPanel.value.status === 'failed' ? (importPanel.value.message || '入关抓取失败') : '入关侧暂无税率'
})

async function doPredict() {
  const d = description.value.trim()
  if (!d) {
    ElMessage.warning('请输入商品描述')
    return
  }
  predicting.value = true
  try {
    result.value = await predictTariff({
      description: d,
      origin_country: originCountry.value,
      dest_country: destCountry.value,
      top_k: 8,
    })
  } catch (e) {
    // 请求拦截器已提示；如为 404 未分类也展示原始响应字段
  } finally {
    predicting.value = false
  }
}

async function doFetchFresh() {
  if (!result.value || !result.value.chosen) return
  refreshing.value = true
  try {
    const r = await fetchTariff({
      hs_code: result.value.chosen.hs_code,
      origin_country: originCountry.value,
      dest_country: destCountry.value,
      force: true,
    })
    result.value.export_panel = r.export_panel
    result.value.import_panel = r.import_panel
    const bad = []
    if (r.export_panel && r.export_panel.status === 'failed') bad.push('出关')
    if (r.import_panel && r.import_panel.status === 'failed') bad.push('入关')
    if (bad.length) ElMessage.warning(`${bad.join('/')}侧抓取失败，可点击表格手动填写`)
    else ElMessage.success('税率已强制刷新')
    loadStats()
  } finally {
    refreshing.value = false
  }
}

async function doSearch() {
  const q = searchQ.value.trim()
  if (!q) {
    ElMessage.warning('请输入检索关键词')
    return
  }
  searching.value = true
  searched.value = true
  try {
    searchResults.value = (await searchTariff({ q, country: searchCountry.value, limit: 20 })).results || []
  } finally {
    searching.value = false
  }
}

async function openDetail(hsCode) {
  detailVisible.value = true
  detail.value = null
  detail.value = await getTariffDetail({ hs_code: hsCode, country: searchCountry.value })
}

async function loadStats() {
  try {
    stats.value = await getTariffStats()
  } catch (e) {
    // 静默
  }
}

async function loadWorldMeta() {
  try {
    worldMeta.value = (await getWorldSources()) || {}
  } catch (e) {
    // 静默
  }
}

async function doWorldAb() {
  const hs = abHs.value.replace(/\s+/g, '')
  if (!hs) {
    ElMessage.warning('请输入 HS 编码或商品名')
    return
  }
  if (abOrigin.value === abDest.value) {
    ElMessage.warning('出口国与进口国不能相同')
    return
  }
  abCandidates.value = null
  abLoading.value = true
  try {
    if (/^\d{6,10}$/.test(hs)) {
      abResult.value = await getWorldAb({ origin: abOrigin.value, dest: abDest.value, hs })
      if (abResult.value.rate == null) {
        ElMessage.warning(abResult.value.note || '该编码暂无税率数据')
      }
    } else {
      const r = await smartSearch({ q: hs, dest: abDest.value, limit: 8 })
      abResult.value = null
      abCandidates.value = r && r.candidates ? r : { ...r, candidates: [] }
      if (r.candidates && r.candidates.length === 1) {
        onCandPick(r.candidates[0])
      }
    }
  } catch (e) {
    // 拦截器已提示
  } finally {
    abLoading.value = false
  }
}

function onCandPick(row) {
  if (!row || !row.hs) return
  abHs.value = row.hs
  abCandidates.value = null
  doWorldAb()
}

onMounted(() => {
  loadStats()
  loadWorldMeta()
})
</script>

<style scoped>
.tariff-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.predict-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.field-label {
  color: #606266;
  font-size: 13px;
  white-space: nowrap;
}

.arrow {
  color: #909399;
  font-weight: 600;
}

.result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}

.route {
  margin-left: auto;
  color: #606266;
  font-size: 13px;
}

.result-reason {
  color: #909399;
  font-size: 12px;
}

.chosen-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #f5f9ff;
  border: 1px solid #d9e8ff;
  border-radius: 8px;
  padding: 14px 16px;
  margin-top: 12px;
}

.chosen-code {
  font-size: 22px;
  font-weight: 700;
  color: #409eff;
  font-family: monospace;
}

.chosen-desc {
  flex: 1;
  color: #303133;
}

.dual-panel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}

.side-panel {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  background: #fafbfd;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}

.disclaimer {
  margin-top: 12px;
  color: #909399;
  font-size: 12px;
}

.card-subtitle {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}

.stats-row {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}

.stat-item {
  min-width: 130px;
}

.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: #409eff;
}

.stat-small {
  font-size: 15px;
  line-height: 34px;
}

.stat-label {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
}

/* A→B 速查 */
.ab-result {
  margin-top: 14px;
  border: 1px solid #d9e8ff;
  border-radius: 8px;
  background: #f5f9ff;
  padding: 12px 14px;
}

.ab-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.ab-provenance {
  font-weight: 600;
  color: #303133;
}

.ab-hs {
  font-family: monospace;
  color: #409eff;
  font-weight: 600;
}

.ab-source {
  background: #ecf5ff;
  color: #409eff;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
}

.ab-asof {
  color: #909399;
  font-size: 12px;
  margin-left: auto;
}

.ab-rate-line {
  margin-top: 8px;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.ab-rate {
  font-size: 30px;
  font-weight: 700;
  color: #409eff;
}

.ab-rate.zero {
  color: #67c23a;
}

.ab-rate-text {
  color: #606266;
  font-size: 14px;
}

.ab-note {
  margin-top: 6px;
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
}

/* 商品名模糊候选 */
.ab-cand-wrap {
  margin-top: 14px;
}

.ab-cand-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
  color: #606266;
}

.ab-cand-title {
  font-weight: 600;
  color: #303133;
}

.ab-cand-hint {
  color: #909399;
  font-size: 12px;
}

:deep(.ab-cand-clickable) {
  cursor: pointer;
}

/* world_mfn 覆盖 */
.world-meta {
  border-top: 1px dashed #ebeef5;
  margin-top: 12px;
}

.world-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.world-count-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafbfd;
  padding: 6px 10px;
  font-size: 13px;
  color: #606266;
}

.world-count-item b {
  color: #303133;
}
</style>