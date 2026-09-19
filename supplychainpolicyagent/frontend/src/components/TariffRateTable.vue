<template>
  <div>
    <el-table :data="displayRows" size="small" border>
      <el-table-column label="税种" width="110">
        <template #default="{ row }">
          <el-select v-if="editMode" v-model="row.duty_type" size="small" style="width: 100px">
            <el-option v-for="(label, value) in DUTY_OPTIONS" :key="value" :label="label" :value="value" />
          </el-select>
          <span v-else>{{ dutyLabel(row.duty_type) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="税率" min-width="110">
        <template #default="{ row }">
          <el-input v-if="editMode" v-model="row.duty_rate" size="small" placeholder="如 2.5% / FREE" />
          <span v-else>{{ row.duty_rate || '--' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="适用范围" min-width="80">
        <template #default="{ row }">
          <el-input v-if="editMode" v-model="row.trade_partner" size="small" />
          <span v-else>{{ row.trade_partner || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="说明/来源" min-width="120">
        <template #default="{ row }">
          <span class="muted">{{ row.note || (row.source === 'manual' ? '人工修正' : '在线抓取') }}</span>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="editMode" class="edit-bar">
      <el-button type="primary" size="small" :loading="saving" @click="save">保存修正</el-button>
      <el-button size="small" @click="editMode = false">取消</el-button>
      <span class="muted">抓取失败或数据不准时，可手填覆盖（本地保存，抓取不会覆盖）</span>
    </div>
    <el-button v-else link type="primary" size="small" style="margin-top: 8px" @click="enterEdit">人工修正</el-button>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { saveManualRates } from '@/api/tariff'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  hsCode: { type: String, default: '' },
  country: { type: String, default: 'CN' },
  direction: { type: String, default: 'export' },
})

const DUTY_OPTIONS = {
  export_duty: '出口关税',
  export_provisional: '出口暂定税',
  export_rebate: '出口退税',
  import_mfn: '最惠国税率',
  import_general: '普通税率',
  import_provisional: '进口暂定税率',
  preferential: '协定税率',
  add: '反倾销税',
  countervail: '反补贴税',
  safeguard: '保障措施税',
  vat: '增值税',
  excise: '消费税',
  other: '其他',
}

const editMode = ref(false)
const saving = ref(false)

const displayRows = computed(() => props.rows || [])

function dutyLabel(t) {
  return DUTY_OPTIONS[t] || t || '其他'
}

function enterEdit() {
  if (!displayRows.value.length) {
    displayRows.value = [{ duty_type: 'other', duty_rate: '', trade_partner: '' }]
  }
  editMode.value = true
}

async function save() {
  saving.value = true
  try {
    const rows = displayRows.value
      .map((r) => ({
        duty_type: r.duty_type || 'other',
        duty_rate: (r.duty_rate || '').toString(),
        trade_partner: r.trade_partner || '',
      }))
      .filter((r) => r.duty_rate.trim())
    if (!rows.length) {
      ElMessage.warning('至少填写一条税率')
      return
    }
    await saveManualRates({
      hs_code: props.hsCode,
      country: props.country,
      direction: props.direction,
      rows,
    })
    ElMessage.success('人工修正已保存')
    editMode.value = false
  } catch (e) {
    // 拦截器已提示
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.edit-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.muted {
  color: #909399;
  font-size: 12px;
}
</style>