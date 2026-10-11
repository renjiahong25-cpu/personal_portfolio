<template>
  <div class="quote-page">
    <!-- 输入卡片 -->
    <div class="panel-card">
      <div class="card-title">对美报价成本重算</div>
      <div class="quote-row">
        <el-input
          v-model="description"
          placeholder="输入商品描述，如：26寸电动摩托车 / LED 户外投光灯 / 木制办公桌"
          clearable
          style="flex: 1"
          @keyup.enter="doQuote()"
        />
        <el-input-number
          v-model="valueUsd"
          :min="1"
          :max="9999999"
          :precision="2"
          :step="100"
          :controls="false"
          style="width: 150px"
          placeholder="货值 USD"
        />
        <span class="field-label">USD</span>
        <el-select v-model="mode" style="width: 170px">
          <el-option v-for="m in MODES" :key="m.value" :label="m.label" :value="m.value" />
        </el-select>
        <el-select v-model="incoterm" style="width: 100px">
          <el-option label="FOB" value="FOB" />
          <el-option label="EXW" value="EXW" />
          <el-option label="CIF" value="CIF" />
          <el-option label="DDP" value="DDP" />
        </el-select>
        <el-button type="primary" :loading="loading" @click="doQuote()">重算报价</el-button>
      </div>
      <template v-if="quoteCandidates.length">
        <div class="cand-strip">
          <span class="cand-label">未精确匹配到商品，请选择最接近的候选（或重新输入更具体的描述）：</span>
          <el-button v-for="c in quoteCandidates" :key="'q' + c.hs" size="small" @click="pickQuoteCand(c)">
            {{ c.hs }} {{ c.name_cn || c.name_en }}
          </el-button>
        </div>
      </template>
      <div class="disclaimer">
        2026 政策快照口径：基础 MFN + 301 附加 + 对等关税（版本 {{ version?.extra_version || '--' }}）
        · 数据为参考值，实际以报关当日 USTR/CBP 现行税则为准。
      </div>
    </div>

    <!-- 多中转路径报价 -->
    <div class="panel-card">
      <div class="card-title">
        多中转路径报价（CN → 中转* → US）
        <el-tag size="small" type="warning" effect="plain" style="margin-left: 8px">逐段递推</el-tag>
      </div>
      <div class="quote-row">
        <el-input
          v-model="routeDesc"
          placeholder="输入商品描述，如：26寸电动摩托车 / LED 户外投光灯"
          clearable
          style="flex: 1"
          @keyup.enter="doQuoteRoute()"
        />
        <el-input-number
          v-model="routeValue"
          :min="1"
          :max="9999999"
          :precision="2"
          :step="100"
          :controls="false"
          style="width: 150px"
          placeholder="货值 USD"
        />
        <span class="field-label">USD</span>
        <el-select v-model="routePreset" style="width: 240px" placeholder="选择预置路径">
          <el-option
            v-for="(label, key) in ROUTE_PRESET_LABELS"
            :key="key"
            :label="label"
            :value="key.split('->')"
          />
        </el-select>
        <el-button type="primary" :loading="routeLoading" @click="doQuoteRoute()">计算路径</el-button>
        <el-button plain :loading="routeLoading" @click="doQuoteRouteAll()">全部路径对比</el-button>
      </div>
      <template v-if="routeCandidates.length">
        <div class="cand-strip">
          <span class="cand-label">未精确匹配到商品，请选择最接近的候选（或重新输入更具体的描述）：</span>
          <el-button v-for="c in routeCandidates" :key="'r' + c.hs" size="small" @click="pickRouteCand(c)">
            {{ c.hs }} {{ c.name_cn || c.name_en }}
          </el-button>
        </div>
      </template>
      <div class="disclaimer">
        选择预置路径精确计算；「全部路径对比」返回全部 {{ Object.keys(ROUTE_PRESET_LABELS).length }} 条路径（route 省略）。可输入空描述复用上方案例。
      </div>

      <template v-if="routeResult">
        <!-- 地图 + 汇总 -->
        <div class="route-header">
          <div class="route-map-wrap">
            <RouteMap :route="routeResult.custom?.route || activeRouteKey.split('->') || []" :rates="routeRates" :height="280" />
          </div>
          <div class="route-summary">
            <div class="route-summary-title">
              路径：<b>{{ activeRouteKey }}</b>
              <el-tag size="small" effect="plain" style="margin-left: 8px">HS {{ routeResult.chosen?.hs_code }}</el-tag>
            </div>
            <div class="route-desc">{{ routeResult.chosen?.description }}</div>
            <div class="rs-item">
              <span>货值</span>
              <b>${{ routeResult.value_usd }}</b>
            </div>
            <div class="rs-item">
              <span>到岸成本（保守）</span>
              <b>${{ activeLeg?.landed_usd?.conservative?.min }} ~ ${{ activeLeg?.landed_usd?.conservative?.max }}</b>
            </div>
            <div class="rs-item">
              <span>到岸成本（乐观）</span>
              <b>${{ activeLeg?.landed_usd?.optimistic?.min }} ~ ${{ activeLeg?.landed_usd?.optimistic?.max }}</b>
            </div>
          </div>
        </div>

        <!-- 路径对比表 -->
        <div class="route-subtitle">全部路径对比（按保守到岸成本升序）</div>
        <el-table :data="routeRanked" size="small" border :row-class-name="rankRowClass">
          <el-table-column label="路径" min-width="200">
            <template #default="{ row }">
              <span class="route-key">{{ row.route }}</span>
            </template>
          </el-table-column>
          <el-table-column label="保守到岸" width="160">
            <template #default="{ row }">
              ${{ row.landed_min }} ~ ${{ row.landed_max }}
            </template>
          </el-table-column>
          <el-table-column label="乐观到岸(起)" width="140">
            <template #default="{ row }">${{ row.optimistic_min }}</template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button
                size="small"
                link
                type="primary"
                :disabled="row.route === activeRouteKey"
                @click="selectRoute(row.route)"
              >
                {{ row.route === activeRouteKey ? '当前' : '查看' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 逐段明细 -->
        <div class="route-subtitle">逐段成本明细（{{ activeRouteKey }}）</div>
        <el-table :data="activeRouteLegs || []" size="small" border>
          <el-table-column type="index" label="段" width="55" />
          <el-table-column label="起点 → 终点" min-width="130">
            <template #default="{ row }">
              <b>{{ countryLabel(row.from) }} → {{ countryLabel(row.to) }}</b>
            </template>
          </el-table-column>
          <el-table-column label="段性质" width="110">
            <template #default="{ row }">
              <el-tag v-if="row.us_duty" size="small" type="warning" effect="light">美国段</el-tag>
              <el-tag v-else size="small" effect="plain">中转国段</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="运费" min-width="140">
            <template #default="{ row }">
              <span v-if="row.freight_pct">{{ row.freight_pct.min }}%~{{ row.freight_pct.max }}%</span>
              <span v-if="row.freight_usd" class="leg-note">${{ row.freight_usd.min }}~${{ row.freight_usd.max }}</span>
              <span v-if="!row.freight_pct && !row.freight_usd">--</span>
            </template>
          </el-table-column>
          <el-table-column label="税率" width="130">
            <template #default="{ row }">
              <template v-if="row.us_duty">
                保守 {{ row.us_duty.conservative.pct }}% / 乐观 {{ row.us_duty.optimistic.pct }}%
              </template>
              <span v-else>{{ row.import_duty_pct != null ? row.import_duty_pct + '%' : '--' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="关税 $" min-width="140">
            <template #default="{ row }">
              <span v-if="row.us_duty">
                {{ row.us_duty.conservative.usd.min }}~{{ row.us_duty.conservative.usd.max }}
              </span>
              <span v-else-if="row.import_duty_usd">
                ${{ row.import_duty_usd.min }}~${{ row.import_duty_usd.max }}
              </span>
              <span v-else>--</span>
            </template>
          </el-table-column>
          <el-table-column label="操作费 $">
            <template #default="{ row }">
              <span v-if="row.handling_usd">${{ row.handling_usd.min }}~${{ row.handling_usd.max }}</span>
              <span v-else>--</span>
            </template>
          </el-table-column>
          <el-table-column label="到岸价（保守）" min-width="150">
            <template #default="{ row }">
              <span v-if="row.landed_usd">
                <b>${{ row.landed_usd.conservative.min }} ~ ${{ row.landed_usd.conservative.max }}</b>
              </span>
              <span v-else-if="row.customs_value_at_entry" class="leg-note">
                申报值 ${{ row.customs_value_at_entry.min }}~${{ row.customs_value_at_entry.max }}
              </span>
              <span v-else class="leg-note">--</span>
            </template>
          </el-table-column>
          <el-table-column label="备注" min-width="140">
            <template #default="{ row }">
              <span v-if="row.us_duty" class="leg-note">301/对等已并入双情景</span>
              <span v-else-if="row.import_duty_source" class="leg-note">{{ row.import_duty_source }}</span>
              <span v-else class="leg-note">--</span>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="routeResult.flags && routeResult.flags.length" class="warn-list">
          <div v-for="(f, i) in routeResult.flags" :key="i" class="warn-row">
            <el-icon color="#E6A23C"><Warning /></el-icon>
            <span>{{ f }}</span>
          </div>
        </div>
        <div v-if="routeResult.notes && routeResult.notes.length" class="note-list">
          <div v-for="(n, i) in routeResult.notes" :key="i" class="note-row">· {{ n }}</div>
        </div>
      </template>
    </div>

    <template v-if="result">
      <!-- 选中商品 + 综合关税 -->
      <div class="panel-card">
        <div class="card-title">
          当前路径 · {{ result.comprehensive?.label }}
          <el-tag size="small" type="warning" effect="plain" style="margin-left: 8px">
            HS {{ result.chosen?.hs_code }}
          </el-tag>
          <el-tag size="small" effect="plain" style="margin-left: 6px">货值 ${{ result.value_usd }}</el-tag>
        </div>
        <div class="chosen-desc">{{ result.chosen?.description }}</div>
        <div class="comp-grid">
          <div
            v-for="c in result.comprehensive?.components || []"
            :key="c.label"
            class="comp-cell"
          >
            <div class="comp-label">{{ c.label }}</div>
            <div class="comp-pct">{{ c.pct }}%</div>
            <div v-if="c.note" class="comp-note">{{ c.note }}</div>
            <div v-if="c.list && c.list.length" class="comp-note">{{ c.list.join(' / ') }}</div>
            <div v-if="c.partner" class="comp-note">按最终原产国 {{ c.partner }}</div>
          </div>
          <div class="comp-cell comp-total">
            <div class="comp-label">综合关税</div>
            <div class="comp-pct">{{ result.comprehensive?.duty_total }}%</div>
            <div class="comp-note">关税额 ≈ ${{ result.comprehensive?.tariff_amount }}</div>
          </div>
          <div class="comp-cell comp-total">
            <div class="comp-label">到岸成本区间</div>
            <div class="comp-pct comp-small">
              ${{ result.comprehensive?.landed_cost_range?.min }} ~ ${{ result.comprehensive?.landed_cost_range?.max }}
            </div>
            <div class="comp-note">
              运费 {{ result.comprehensive?.freight_pct?.min }}%~{{ result.comprehensive?.freight_pct?.max }}%
              + 处理费 ${{ result.comprehensive?.landed_cost_range?.min ? '' : '' }}
            </div>
          </div>
        </div>
        <div v-if="result.comprehensive?.flag?.length" class="warn-box">
          <el-icon color="#E6A23C"><Warning /></el-icon>
          <span v-for="f in result.comprehensive.flag" :key="f">{{ f }}；</span>
        </div>
        <div v-if="result.comprehensive?.notes?.length" class="note-box">
          <span v-for="n in result.comprehensive.notes" :key="n">· {{ n }}</span>
        </div>
        <div v-if="result.llm_summary" class="llm-box">💡 {{ result.llm_summary }}</div>
      </div>

      <!-- 路径对比 -->
      <div class="panel-card">
        <div class="card-title">五条路径关税与到岸成本对比</div>
        <el-table :data="routeRows" size="small" border>
          <el-table-column prop="label" label="路径" width="150" />
          <el-table-column label="最终原产国" prop="final_origin" width="100" />
          <el-table-column label="基础 MFN" width="90">
            <template #default="{ row }">{{ compOf(row, '基础 MFN') }}%</template>
          </el-table-column>
          <el-table-column label="301 附加" width="90">
            <template #default="{ row }">{{ compOf(row, '301 附加') }}%</template>
          </el-table-column>
          <el-table-column label="对等关税" width="90">
            <template #default="{ row }">{{ compOf(row, '对等关税') }}%</template>
          </el-table-column>
          <el-table-column label="综合关税" width="110">
            <template #default="{ row }">
              <b :class="{ 'risk': row.duty_total >= 30 }">{{ row.duty_total }}%</b>
            </template>
          </el-table-column>
          <el-table-column label="关税额" width="110">
            <template #default="{ row }">${{ row.tariff_amount }}</template>
          </el-table-column>
          <el-table-column label="到岸成本区间" min-width="180">
            <template #default="{ row }">
              ${{ row.landed_cost_range.min }} ~ ${{ row.landed_cost_range.max }}
            </template>
          </el-table-column>
          <el-table-column label="提示" min-width="220">
            <template #default="{ row }">
              <el-tag v-if="row.flag && row.flag.length" size="small" type="warning" effect="light">
                {{ row.flag[0] }}
              </el-tag>
              <el-tag v-else size="small" type="success" effect="plain">常规</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="" width="80">
            <template #default="{ row }">
              <el-button link type="primary" :disabled="row.mode === result.mode" @click="reQuoteMode(row.mode)">
                {{ row.mode === result.mode ? '当前' : '切换' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- HS 自查 -->
      <div class="panel-card">
        <div class="card-title">HS 归错代价自查（候选编码税率差）</div>
        <el-table :data="result.hs_check" size="small" border>
          <el-table-column prop="hs_code" label="候选 HS" width="120" />
          <el-table-column label="品目" min-width="200">
            <template #default="{ row }">{{ row.description }}</template>
          </el-table-column>
          <el-table-column label="匹配度" width="90">
            <template #default="{ row }">{{ row.score ? +(row.score * 100).toFixed(1) : '--' }}%</template>
          </el-table-column>
          <el-table-column label="基础%" width="80" prop="base_rate" />
          <el-table-column label="301%" width="80" prop="extra_301" />
          <el-table-column label="税率合计%" width="100">
            <template #default="{ row }">{{ row.effective_rate }}</template>
          </el-table-column>
          <el-table-column label="关税额(不含对等)" width="150">
            <template #default="{ row }">${{ row.tariff_amount }}</template>
          </el-table-column>
          <el-table-column label="与所选差额" width="130">
            <template #default="{ row }">
              <span :class="{ risk: row.delta_vs_chosen_usd > 0 }">
                {{ row.delta_vs_chosen_usd > 0 ? '+' : '' }}${{ row.delta_vs_chosen_usd }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <div class="disclaimer">
          注：HS 自查关税额不含对等关税，仅对比 基础+301 归错差异；对等关税按最终原产国在路径卡中核算。
        </div>
      </div>

      <!-- 出口效益 -->
      <div class="panel-card">
        <div class="card-title">出口效益（货值 ${{ result.value_usd }}）</div>
        <div class="rebate-grid">
          <div class="rebate-cell">
            <div class="comp-label">出口退税率</div>
            <div class="comp-pct">
              {{ result.export_panel?.rebate_rate != null ? result.export_panel.rebate_rate + '%' : '--' }}
            </div>
            <div class="comp-note">
              {{ result.export_panel?.rebate_amount != null
                ? `约退 $${result.export_panel.rebate_amount}` : (result.export_panel?.message || '未抓取（在线校验在部署环境启用）') }}
            </div>
          </div>
          <div class="rebate-cell">
            <div class="comp-label">净关税成本（关税 - 退税）</div>
            <div class="comp-pct comp-small">
              ${{ result.comprehensive?.tariff_amount != null &&
                 result.export_panel?.rebate_amount != null
                 ? Math.max(0, result.comprehensive.tariff_amount - result.export_panel.rebate_amount).toFixed(2)
                 : result.comprehensive?.tariff_amount || '--' }}
            </div>
            <div class="comp-note">退税高于关税时按 0 显示，具体以税局核定为准</div>
          </div>
        </div>
      </div>
    </template>

    <el-empty v-else-if="submitted" description="未获取报价结果" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { quoteEstimate, getQuoteJob, getQuoteVersions, getQuoteRoute } from '@/api/tariff'
import { ROUTE_PRESET_LABELS, countryLabel } from '@/constants/world'
import RouteMap from '@/components/RouteMap.vue'

const MODES = [
  { value: 'direct', label: '直邮/快递直达' },
  { value: 'direct_sea', label: '海运整拼箱直达' },
  { value: 'vn', label: '转口越南' },
  { value: 'mx', label: '转口墨西哥' },
  { value: 'warehouse', label: '海外仓一件代发' },
]

const description = ref('')
const valueUsd = ref(1000)
const mode = ref('direct')
const incoterm = ref('FOB')
const loading = ref(false)
const submitted = ref(false)
const result = ref(null)
const version = ref(null)
const quoteCandidates = ref([])

// 多中转
const routeDesc = ref('')
const routeValue = ref(1000)
const routePreset = ref(['CN', 'VN', 'US'])
const routeLoading = ref(false)
const routeResult = ref(null)
const activeRouteKey = ref('')
const routeSource = ref('') // preset / all / custom
const routeCandidates = ref([])

const routeRows = computed(() => Object.values(result.value?.routes || {}))

const routeRanked = computed(() => routeResult.value?.ranked || [])

const activeRouteLegs = computed(() => {
  const key = activeRouteKey.value
  if (!key) return []
  if (routeSource.value !== 'all') {
    return routeResult.value?.routes?.[key]?.legs || []
  }
  return routeResult.value?.routes?.[key]?.legs || []
})

const activeLeg = computed(() => {
  const legs = activeRouteLegs.value
  if (!legs || !legs.length) return null
  return legs[legs.length - 1]
})

const routeRates = computed(() => {
  const legs = activeRouteLegs.value || []
  const out = {}
  for (const leg of legs) {
    if (!leg.us_duty && leg.import_duty_pct != null) {
      out[leg.to] = leg.import_duty_pct
    }
  }
  return out
})

function compOf(row, label) {
  const c = (row.components || []).find((x) => x.label === label)
  return c ? c.pct : 0
}

async function doQuote(hsCode = null) {
  const d = description.value.trim()
  if (!d) {
    ElMessage.warning('请输入商品描述')
    return
  }
  if (!valueUsd.value || valueUsd.value <= 0) {
    ElMessage.warning('请输入货值（>0）')
    return
  }
  loading.value = true
  submitted.value = true
  quoteCandidates.value = []
  try {
    const res = await quoteEstimate({
      description: d,
      value_usd: valueUsd.value,
      mode: mode.value,
      incoterm: incoterm.value,
      origin_country: 'CN',
      dest_country: 'US',
      top_k: 8,
      with_llm: false,
      hs_code: hsCode,
    })
    if (res && res.status === 'queued' && res.job_id) {
      await pollJob(res.job_id)
    } else if (res && res.status === 'no_classify') {
      result.value = null
      quoteCandidates.value = res.candidates_suggest || []
      if (!quoteCandidates.value.length) ElMessage.warning(res.message || '未匹配到商品，请重新输入更具体的描述')
    } else {
      result.value = res
      ElMessage.success(`报价完成：综合关税 ${res.comprehensive.duty_total}%`)
    }
  } catch (e) {
    result.value = null
    const d = e?.response?.data?.detail
    ElMessage.error(typeof d === 'string' ? d : Array.isArray(d) && d[0]?.msg ? d[0].msg : '报价请求失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function pickQuoteCand(c) {
  if (!c) return
  description.value = c.name_cn || c.name_en || c.hs
  doQuote(c.hs)
}

async function pollJob(jobId) {
  const deadline = Date.now() + 60 * 1000
  while (Date.now() < deadline) {
    const body = await getQuoteJob(jobId).catch(() => null)
    if (body && body.status === 'done') {
      if (body.result && body.result.status === 'no_classify') {
        result.value = null
        quoteCandidates.value = body.result.candidates_suggest || []
        return
      }
      result.value = body.result
      ElMessage.success(`报价完成：综合关税 ${body.result.comprehensive.duty_total}%`)
      return
    }
    if (body && body.status === 'failed') {
      ElMessage.error(`报价处理失败：${body.error || '未知错误'}`)
      return
    }
    await sleep(1000)
  }
  ElMessage.warning('排队处理超时，请稍后重试')
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function reQuoteMode(m) {
  mode.value = m
  doQuote()
}

async function loadVersions() {
  try {
    version.value = await getQuoteVersions()
  } catch (e) {
    // 静默
  }
}

function ensureRouteDesc() {
  const d = routeDesc.value.trim() || description.value.trim()
  if (!d) {
    ElMessage.warning('请输入商品描述')
    return null
  }
  return d
}

async function doQuoteRoute(hsCode = null) {
  const d = ensureRouteDesc()
  if (!d) return
  if (!routeValue.value || routeValue.value <= 0) {
    ElMessage.warning('请输入货值（>0）')
    return
  }
  routeLoading.value = true
  routeCandidates.value = []
  try {
    const res = await getQuoteRoute({
      description: d,
      value_usd: routeValue.value,
      route: routePreset.value,
      top_k: 8,
      with_llm: false,
      hs_code: hsCode,
    })
    consumeRouteResult(res, 'preset')
  } catch (e) {
    // 拦截器已提示
  } finally {
    routeLoading.value = false
  }
}

async function doQuoteRouteAll(hsCode = null) {
  const d = ensureRouteDesc()
  if (!d) return
  if (!routeValue.value || routeValue.value <= 0) {
    ElMessage.warning('请输入货值（>0）')
    return
  }
  routeLoading.value = true
  routeCandidates.value = []
  try {
    const res = await getQuoteRoute({
      description: d,
      value_usd: routeValue.value,
      top_k: 8,
      with_llm: false,
      hs_code: hsCode,
    })
    consumeRouteResult(res, 'all')
  } catch (e) {
    // 拦截器已提示
  } finally {
    routeLoading.value = false
  }
}

function pickRouteCand(c) {
  if (!c) return
  routeDesc.value = c.name_cn || c.name_en || c.hs
  doQuoteRouteAll(c.hs)
}

async function consumeRouteResult(res, source) {
  if (!res) return
  if (res.status === 'no_classify') {
    routeResult.value = null
    routeCandidates.value = res.candidates_suggest || []
    if (!routeCandidates.value.length) ElMessage.warning(res.message || '未匹配到商品，请重新输入更具体的描述')
    return
  }
  if (res.status === 'queued' && res.job_id) {
    const deadline = Date.now() + 60 * 1000
    while (Date.now() < deadline) {
      const body = await getQuoteJob(res.job_id).catch(() => null)
      if (body && body.status === 'done') {
        if (body.result && body.result.status === 'no_classify') {
          routeResult.value = null
          routeCandidates.value = body.result.candidates_suggest || []
          return
        }
        routeResult.value = body.result
        source = body.result?.route_mode === 'presets' ? 'all' : source
        routeSource.value = source
        pickFirstRoute()
        ElMessage.success('多中转路径计算完成')
        return
      }
      if (body && body.status === 'failed') {
        ElMessage.error(`排队处理失败：${body.error || '未知错误'}`)
        return
      }
      await sleep(1000)
    }
    ElMessage.warning('排队处理超时，请稍后重试')
    return
  }
  routeCandidates.value = []
  routeResult.value = res
  routeSource.value = source
  pickFirstRoute()
  ElMessage.success('多中转路径计算完成')
}

function pickFirstRoute() {
  const ranked = routeResult.value?.ranked
  const keys = ranked && ranked.length ? ranked.map((r) => r.route) : Object.keys(routeResult.value?.routes || {})
  if (keys.length) activeRouteKey.value = keys[0]
  else activeRouteKey.value = routeResult.value?.comprehensive ? Object.keys(routeResult.value.routes)[0] : ''
}

function selectRoute(key) {
  activeRouteKey.value = key
  if (routeSource.value === 'preset' && routeResult.value?.routes && routeResult.value.routes[key]) {
    const parts = key.split('->').filter(Boolean)
    if (parts.length >= 2) routePreset.value = parts
  }
}

function rankRowClass({ row }) {
  return row.route === activeRouteKey.value ? 'route-active-row' : ''
}

onMounted(loadVersions)
</script>

<style scoped>
.quote-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 16px;
  background: #fff;
}

.cand-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 10px 12px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
}

.cand-label {
  color: #e6a23c;
  font-size: 13px;
  font-weight: 600;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #303133;
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.quote-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.field-label {
  color: #606266;
  font-size: 13px;
  white-space: nowrap;
}

.disclaimer {
  margin-top: 10px;
  color: #909399;
  font-size: 12px;
}

.chosen-desc {
  color: #606266;
  font-size: 13px;
  margin-bottom: 12px;
}

.comp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

.comp-cell {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  background: #fafbfd;
}

.comp-total {
  background: #f5f9ff;
  border-color: #d9e8ff;
}

.comp-label {
  color: #909399;
  font-size: 12px;
}

.comp-pct {
  font-size: 24px;
  font-weight: 700;
  color: #409eff;
  margin: 4px 0;
}

.comp-small {
  font-size: 16px;
  line-height: 30px;
}

.comp-note {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
}

.warn-box {
  margin-top: 10px;
  padding: 8px 12px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  color: #e6a23c;
  font-size: 13px;
  display: flex;
  gap: 6px;
  align-items: flex-start;
}

.note-box {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.llm-box {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 6px;
  color: #67c23a;
  font-size: 13px;
}

.rebate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}

.rebate-cell {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  background: #fafbfd;
}

.risk {
  color: #f56c6c;
  font-weight: 700;
}

/* 多中转 */
.route-header {
  display: flex;
  gap: 14px;
  margin-top: 12px;
}

.route-map-wrap {
  flex: 1;
  min-width: 0;
}

.route-summary {
  width: 280px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafbfd;
  padding: 12px;
}

.route-summary-title {
  font-size: 14px;
  color: #303133;
}

.route-summary-title b {
  color: #409eff;
}

.route-desc {
  color: #909399;
  font-size: 12px;
  margin: 4px 0 10px;
}

.rs-item {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  padding: 4px 0;
  border-bottom: 1px dashed #ebeef5;
}

.rs-item span {
  color: #909399;
}

.route-subtitle {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin: 16px 0 8px;
}

.route-key {
  font-family: monospace;
  font-size: 13px;
}

.route-active-row {
  background: #f5f9ff !important;
}

.leg-note {
  color: #909399;
  font-size: 12px;
}

.warn-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.warn-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 6px;
  padding: 6px 10px;
  color: #e6a23c;
  font-size: 13px;
}

.note-list {
  margin-top: 10px;
  color: #909399;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>