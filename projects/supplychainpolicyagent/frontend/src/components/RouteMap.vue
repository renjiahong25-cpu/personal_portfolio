<template>
  <div class="route-map" :style="{ height: heightNum + 'px' }" :class="{ empty: !hasNodes }">
    <div v-if="!hasNodes" class="route-map-empty">选择路径后展示地图连线</div>
    <div v-else ref="chartEl" class="route-map-chart"></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import worldGeo from '@/assets/world.json'

const props = defineProps({
  route: {
    type: Array,
    default: () => [],
  },
  rates: {
    type: Object,
    default: () => {},
  },
  height: {
    type: Number,
    default: 320,
  },
})

const chartEl = ref(null)
let chart = null
const heightNum = ref(320)

const COUNTRIES = [
  { code: 'CN', name: '中国', coord: [104.2, 35.0] },
  { code: 'SG', name: '新加坡', coord: [103.85, 1.35] },
  { code: 'MY', name: '马来西亚', coord: [109.0, 4.2] },
  { code: 'TH', name: '泰国', coord: [101.5, 15.9] },
  { code: 'MX', name: '墨西哥', coord: [-102.5, 23.5] },
  { code: 'US', name: '美国', coord: [-98.0, 39.0] },
  { code: 'DE', name: '德国', coord: [10.4, 51.1] },
]

const hasNodes = computed(() => (props.route || []).length >= 2)

function build() {
  const codes = (props.route || []).map((c) => String(c).toUpperCase())
  const nodes = codes
    .map((code) => COUNTRIES.find((x) => x.code === code))
    .filter(Boolean)
  const data = nodes.map((n) => ({ name: n.name, code: n.code, value: [...n.coord] }))
  const links = []
  for (let i = 0; i + 1 < nodes.length; i++) {
    links.push({ coords: [nodes[i].coord, nodes[i + 1].coord], fromName: nodes[i].name, toName: nodes[i + 1].name, fromCode: nodes[i].code, toCode: nodes[i + 1].code })
  }
  return { data, links }
}

function render() {
  if (!chartEl.value || !chart) return
  const { data, links } = build()
  if (!links.length) {
    chart.clear()
    return
  }
  chart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        if (p.seriesType === 'effectScatter') return p.name
        if (p.seriesType === 'lines') {
          const code = p.data.toCode || p.data.toName
          const r = props.rates ? props.rates[code] : undefined
          let extra = ''
          if (r !== undefined && r !== null) extra = `<br/>进口税率 ${typeof r === 'number' ? r + '%' : r}`
          return `${p.data.fromName} → ${p.data.toName}` + extra
        }
        return p.name
      },
    },
    geo: {
      map: 'world',
      roam: true,
      zoom: 1.2,
      itemStyle: { areaColor: '#e8eef7', borderColor: '#b6c6dd', borderWidth: 0.6 },
      emphasis: { itemStyle: { areaColor: '#d5e2f5' }, label: { show: false } },
      select: { disabled: true },
    },
    series: [
      {
        type: 'lines',
        coordinateSystem: 'geo',
        data: links,
        lineStyle: { color: '#409EFF', width: 2, curveness: 0.15, opacity: 0.9 },
        effect: { show: true, period: 4, trailLength: 0.4, symbol: 'arrow', symbolSize: 7, color: '#F56C6C' },
      },
      {
        type: 'effectScatter',
        coordinateSystem: 'geo',
        data,
        symbolSize: 9,
        rippleEffect: { brushType: 'stroke', scale: 3.2 },
        label: { show: true, position: 'right', formatter: (p) => p.name, fontSize: 11, color: '#303133' },
        itemStyle: { color: '#67C23A' },
      },
    ],
  })
}

function ensureChart() {
  if (!chart && chartEl.value) {
    if (!echarts.getMap('world')) echarts.registerMap('world', worldGeo)
    chart = echarts.init(chartEl.value)
  }
  render()
}

watch(() => props.route, () => nextTick(ensureChart), { deep: true })
watch(() => props.rates, () => nextTick(ensureChart), { deep: true })
watch(() => props.height, (v) => {
  heightNum.value = v || 320
  chart?.resize()
})

onMounted(async () => {
  heightNum.value = props.height || 320
  await nextTick()
  ensureChart()
})

onBeforeUnmount(() => {
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<style scoped>
.route-map {
  width: 100%;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #fbfcfe;
}

.route-map-chart {
  width: 100%;
  height: 100%;
}

.route-map-empty {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  font-size: 13px;
}
</style>