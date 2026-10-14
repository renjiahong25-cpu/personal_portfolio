<script setup>
/**
 * Module 2 · pop to piano: a recording (typically a pop song) turned into a
 * playable piano arrangement.
 *
 * The pipeline keeps the transcribed melody in the right hand and rebuilds the
 * left hand. The parameters are the arrangement's own: the model, the arranging
 * mode (hybrid vs arranged), the difficulty budget, the left-hand pattern or
 * texture, and the analysis length. There is deliberately no chord-filling
 * option here - it only acts in the faithful piano-transcription module, and the
 * backend refuses it on this endpoint.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  arrangeScore,
  cacheAudioUrl,
  fetchArrangeOptions,
  fetchArrangeSections,
  fetchCacheStatus,
  uploadAndArrange,
} from '../api'
import ScoreResult from './ScoreResult.vue'
import SourcePicker from './SourcePicker.vue'
import TranscriberPicker from './TranscriberPicker.vue'
import { takePendingKey } from '../composables/useScoreHandoff'

const options = ref(null)
const tracks = ref([])
const selectedKey = ref('')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const originalAudioUrl = ref('')

const transcriber = ref('')
const mode = ref('arranged')
const difficulty = ref(3)
const pattern = ref('')
const texture = ref('')
const texturePlan = ref('')
const accompaniment = ref('light')
const previewSeconds = ref(30)

const sections = ref(null)
const sectionsBusy = ref(false)
const sectionTextures = ref({})

const labelZh = {
  intro: '前奏',
  verse: '主歌',
  chorus: '副歌',
  interlude: '间奏',
  bridge: 'bridge',
  outro: '尾奏',
}

const labelColors = {
  intro: '#475569',
  verse: '#2563eb',
  chorus: '#d97706',
  interlude: '#7c3aed',
  bridge: '#059669',
  outro: '#475569',
}

const accompanimentLabels = {
  light: '轻盈',
  standard: '标准',
  full: '浓厚',
}

const accompanimentEntries = computed(() => {
  const names = options.value?.accompaniments || ['light', 'standard', 'full']
  return names.map((value) => ({
    value,
    label: accompanimentLabels[value] || value,
    hint: options.value?.accompanimentHints?.[value] || '',
  }))
})

const presentLabels = computed(() => {
  const seen = []
  for (const s of sections.value?.sections || []) {
    if (!seen.includes(s.label)) seen.push(s.label)
  }
  return seen
})

const textureChoices = computed(() => {
  const labels = { '': '自动（按段落特征）' }
  if (options.value?.textures) {
    for (const [key, label] of Object.entries(options.value.textures)) labels[key] = label
  }
  return labels
})

function defaultTextureFor(label, highMotif = false) {
  const defaults = options.value?.sectionTexturesDefaults || {}
  if (label === 'intro') {
    return highMotif ? defaults.intro || 'arplong' : defaults.introNoMotif || 'bass'
  }
  return defaults[label] || 'half'
}

function sectionTooltip(s) {
  const cov =
    s.evidence?.vocalCoverage == null ? '无人声证据' : `人声覆盖 ${Math.round(s.evidence.vocalCoverage * 100)}%`
  return `${labelZh[s.label] || s.label} 第 ${s.startBar + 1}-${s.endBar} 小节 · 置信 ${Math.round(
    s.confidence * 100,
  )}%${s.highMotif ? ' · 高音动机' : ''} · ${cov}`
}

async function detectSections() {
  if (!selectedKey.value) return
  sectionsBusy.value = true
  try {
    sections.value = await fetchArrangeSections(selectedKey.value)
    sectionTextures.value = {}
    for (const s of sections.value.sections) {
      if (!(s.label in sectionTextures.value)) {
        sectionTextures.value[s.label] = defaultTextureFor(s.label, s.highMotif)
      }
    }
  } catch (err) {
    error.value = `段落检测失败：${err.message}`
  } finally {
    sectionsBusy.value = false
  }
}

function clearSections() {
  sections.value = null
  sectionTextures.value = {}
}

/** The arranging modes on offer, from the backend so there is one source of truth. */
const modeEntries = computed(() =>
  (options.value?.fidelities || ['hybrid', 'arranged']).map((value) => ({
    value,
    label: options.value?.fidelityHints?.[value] || value,
  }))
)

const difficultyEntries = computed(() => {
  const map = options.value?.difficulties || {}
  return Object.entries(map).map(([level, hint]) => ({ level, hint }))
})

const patternLabels = {
  '': '自动判断',
  root_only: '单音根音',
  root_octave: '根音 + 八度',
  block_chord: '柱式和弦',
  broken_chord: '分解和弦',
  alberti_bass: '阿尔贝蒂低音',
}

const textureLabels = computed(() => {
  if (!options.value?.textures) return {}
  const labels = { '': '默认（pattern 系统）' }
  for (const [key, label] of Object.entries(options.value.textures)) {
    labels[key] = label
  }
  return labels
})

async function loadOptions() {
  try {
    options.value = await fetchArrangeOptions()
    if (options.value?.defaultAccompaniment) {
      accompaniment.value = options.value.defaultAccompaniment
    }
  } catch (err) {
    error.value = `无法读取可选项：${err.message}`
  }
}

async function loadTracks() {
  try {
    const status = await fetchCacheStatus()
    tracks.value = status.entries || []
  } catch (err) {
    error.value = `无法读取缓存：${err.message}`
  }
}

function reset() {
  result.value = null
  originalAudioUrl.value = ''
  error.value = ''
}

function applyPayload(payload) {
  result.value = payload
  const key = payload?.upload?.key || payload?.cacheKey
  if (key) originalAudioUrl.value = cacheAudioUrl(key)
}

async function generate() {
  if (!selectedKey.value) return
  busy.value = true
  reset()
  try {
    const hasSections = Object.keys(sectionTextures.value).length > 0
    const payload = await arrangeScore({
      cacheKey: selectedKey.value,
      fidelity: mode.value,
      difficulty: difficulty.value,
      pattern: hasSections ? '' : pattern.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
      texture: hasSections ? '' : texture.value,
      texturePlan: hasSections ? '' : texturePlan.value,
      accompaniment: accompaniment.value,
      sectionTextures: hasSections ? sectionTextures.value : undefined,
    })
    applyPayload(payload)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function onFilePicked(file) {
  // A new recording has different sections: the detected ones belong to the
  // previous track and are cleared before the one-shot upload runs.
  clearSections()
  busy.value = true
  reset()
  try {
    const payload = await uploadAndArrange(file, {
      fidelity: mode.value,
      difficulty: difficulty.value,
      pattern: pattern.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
      texture: texture.value,
      texturePlan: texturePlan.value,
      accompaniment: accompaniment.value,
    })
    applyPayload(payload)
    await loadTracks()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

watch(selectedKey, () => {
  // Switching the source must not leave the previous score - or its sections -
  // on screen.
  reset()
  clearSections()
})

onMounted(async () => {
  await Promise.all([loadOptions(), loadTracks()])
  const pending = takePendingKey()
  if (pending && tracks.value.some((t) => t.key === pending)) {
    selectedKey.value = pending
  }
})
</script>

<template>
  <div class="score-page">
    <section class="panel">
      <h2>1 · 选择音源</h2>
      <SourcePicker
        v-model:selected-key="selectedKey"
        :tracks="tracks"
        :busy="busy"
        generate-label="生成钢琴谱"
        @generate="generate"
        @file="onFilePicked"
      />
    </section>

    <section class="panel">
      <h2>2 · 转录模型</h2>
      <TranscriberPicker :options="options" v-model="transcriber" />
    </section>

    <section class="panel">
      <h2>3 · 编排方式</h2>
      <p class="intro">
        右手保留转录出来的旋律，左手按和弦重建为可演奏的伴奏；旋律本身永远不会被生成内容替换。
      </p>
      <div class="grid">
        <label>
          编排模式
          <select v-model="mode">
            <option v-for="entry in modeEntries" :key="entry.value" :value="entry.value">
              {{ entry.label }}
            </option>
          </select>
        </label>
        <label>
          左手织体
          <select v-model="pattern" :disabled="Boolean(texture) || Boolean(sections)">
            <option v-for="(label, value) in patternLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
        <label>
          伴奏织体
          <select v-model="texture" :disabled="Boolean(sections)">
            <option v-for="(label, value) in textureLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
        <label>
          分析长度
          <select v-model.number="previewSeconds">
            <option :value="30">前 30 秒</option>
            <option :value="60">前 60 秒</option>
            <option :value="120">前 2 分钟</option>
            <option :value="0">整首</option>
          </select>
        </label>
      </div>

      <p v-if="texture" class="hint">
        伴奏织体已启用：左手将使用「{{ textureLabels[texture] }}」织体生成，而非传统的 pattern 系统。
      </p>
      <p v-else class="hint">
        「混合」只重写质量不合格的小节；「全部重配」会按和弦重写所有小节的左手。
        织体系统提供 8 种伴奏纹理：柱式、打击感332、节奏动机、半分解、低音线条、全分解长线条副旋律、全分解和弦琶音、riff乐段动机。
      </p>

      <div class="levels">
        <button
          v-for="entry in difficultyEntries"
          :key="entry.level"
          :class="{ active: difficulty === Number(entry.level) }"
          :title="entry.hint"
          @click="difficulty = Number(entry.level)"
        >
          L{{ entry.level }}
        </button>
      </div>
      <p class="hint">{{ difficultyEntries.find((d) => d.level === String(difficulty))?.hint }}</p>

      <h3 class="subhead">伴奏密度</h3>
      <p class="intro">左手太密会盖住右手旋律。轻盈档按织体角色削减左手音符并降低音量。</p>
      <div class="levels">
        <button
          v-for="entry in accompanimentEntries"
          :key="entry.value"
          :class="{ active: accompaniment === entry.value }"
          :title="entry.hint"
          @click="accompaniment = entry.value"
        >
          {{ entry.label }}
        </button>
      </div>
      <p class="hint">{{
        accompanimentEntries.find((e) => e.value === accompaniment)?.hint
      }}</p>

      <h3 class="subhead">段落织体</h3>
      <p class="intro">
        自动把歌曲分成前奏 / 主歌 / 副歌 / 间奏 / bridge / 尾奏，每段可用不同的左手织体。
        首次检测约需 30-60 秒（Whisper 人声证据），之后按歌曲缓存秒回。仅支持缓存曲目。
      </p>
      <div class="levels">
        <button
          :disabled="!selectedKey || busy || sectionsBusy"
          @click="detectSections"
        >
          {{ sectionsBusy ? '检测中…' : sections ? '重新检测段落' : '检测段落' }}
        </button>
      </div>

      <template v-if="sections">
        <div class="strip">
          <div
            v-for="s in sections.sections"
            :key="s.startBar"
            class="strip-block"
            :style="{ flexGrow: s.endBar - s.startBar, background: labelColors[s.label] }"
            :title="sectionTooltip(s)"
          >
            {{ labelZh[s.label] || s.label }}
          </div>
        </div>
        <p v-if="!sections.vocal.available" class="hint">
          无人声证据（未装 ML 环境或检测失败）：前奏 / 间奏 / 尾奏按能量谷判断，置信度已下调。
        </p>
        <p v-else class="hint">
          人声语言：{{ sections.vocal.language || '未识别' }}
        </p>
        <div class="grid section-grid">
          <label v-for="label in presentLabels" :key="label">
            {{ labelZh[label] || label }}
            <select v-model="sectionTextures[label]">
              <option v-for="(name, key) in textureChoices" :key="key" :value="key">
                {{ name }}
              </option>
            </select>
          </label>
        </div>
      </template>
    </section>

    <div v-if="error" class="panel error-panel">{{ error }}</div>

    <section v-if="result" class="panel">
      <h2>4 · 乐谱</h2>
      <ScoreResult :result="result" :original-audio-url="originalAudioUrl" />
    </section>
  </div>
</template>

<style scoped>
.score-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

h2 {
  margin: 0 0 10px;
  font-size: 15px;
  color: var(--text);
}

.intro {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

select {
  background: var(--glass-1);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--pill);
  padding: 8px 14px;
  min-width: 220px;
}

.subhead {
  margin: 18px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.levels {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.levels button.active {
  background: #fff;
  border-color: transparent;
  color: #09090b;
  font-weight: 600;
}

.strip {
  display: flex;
  gap: 3px;
  margin-top: 12px;
  height: 34px;
}

.strip-block {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  border-radius: 5px;
  color: #fff;
  font-size: 11px;
  overflow: hidden;
  white-space: nowrap;
  cursor: default;
}

.section-grid {
  margin-top: 12px;
}

.hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.error-panel {
  border-color: var(--err);
  color: var(--err);
}
</style>
