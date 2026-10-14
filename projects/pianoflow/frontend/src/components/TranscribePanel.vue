<script setup>
/**
 * Module 1 · piano transcription: a piano recording straight into a faithful
 * score.
 *
 * The parameters are the transcription's own: the model, the faithful mode
 * (transcribed vs as-played), the difficulty budget (as-played only), chord
 * filling (transcribed only) and the analysis length. There is deliberately no
 * accompaniment option here - patterns and textures belong to the pop-to-piano
 * panel, and the backend refuses them on this endpoint.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  cacheAudioUrl,
  fetchCacheStatus,
  fetchTranscribeOptions,
  transcribeScore,
  uploadAndTranscribe,
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
const mode = ref('transcribed')
const difficulty = ref(3)
const fillChords = ref(false)
const previewSeconds = ref(30)

/** The faithful modes on offer, from the backend so there is one source of truth. */
const modeEntries = computed(() =>
  (options.value?.fidelities || ['transcribed', 'as-played']).map((value) => ({
    value,
    label: options.value?.fidelityHints?.[value] || value,
  }))
)

const difficultyEntries = computed(() => {
  const map = options.value?.difficulties || {}
  return Object.entries(map).map(([level, hint]) => ({ level, hint }))
})

async function loadOptions() {
  try {
    options.value = await fetchTranscribeOptions()
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
    const payload = await transcribeScore({
      cacheKey: selectedKey.value,
      fidelity: mode.value,
      difficulty: difficulty.value,
      fillChords: fillChords.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
    })
    applyPayload(payload)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function onFilePicked(file) {
  busy.value = true
  reset()
  try {
    const payload = await uploadAndTranscribe(file, {
      fidelity: mode.value,
      difficulty: difficulty.value,
      fillChords: fillChords.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
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
  // Switching the source must not leave the previous score on screen.
  reset()
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
        generate-label="转录乐谱"
        @generate="generate"
        @file="onFilePicked"
      />
    </section>

    <section class="panel">
      <h2>2 · 转录模型</h2>
      <TranscriberPicker :options="options" v-model="transcriber" />
    </section>

    <section class="panel">
      <h2>3 · 转录模式</h2>
      <div class="grid">
        <label>
          模式
          <select v-model="mode">
            <option v-for="entry in modeEntries" :key="entry.value" :value="entry.value">
              {{ entry.label }}
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

      <p class="hint">
        适合音源本身就是钢琴的时候：音符忠实于演奏，不生成任何伴奏织体。
        「钢琴原谱」严格不改音符，只在和弦一只手够不到时报告而不移动；
        「原样转录」保留每个小节的演奏，仍按难度预算削减，适合想把钢琴谱降难度。
      </p>

      <div class="levels">
        <button
          v-for="entry in difficultyEntries"
          :key="entry.level"
          :class="{ active: difficulty === Number(entry.level) }"
          :disabled="mode !== 'as-played'"
          :title="mode !== 'as-played' ? '钢琴原谱不按难度改变音符' : entry.hint"
          @click="difficulty = Number(entry.level)"
        >
          L{{ entry.level }}
        </button>
      </div>
      <p v-if="mode === 'as-played'" class="hint">
        {{ difficultyEntries.find((d) => d.level === String(difficulty))?.hint }}
      </p>

      <label class="row inline" :class="{ off: mode !== 'transcribed' }">
        <input v-model="fillChords" type="checkbox" :disabled="mode !== 'transcribed'" />
        补齐和弦：把单个和弦音补成三和弦（会增加音符数）
      </label>
      <p v-if="mode !== 'transcribed'" class="hint">「补齐和弦」只对「钢琴原谱」生效。</p>
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

.levels button.active {
  background: #fff;
  border-color: transparent;
  color: #09090b;
  font-weight: 600;
}

.hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.row.inline {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
  font-size: 13px;
  color: var(--muted);
}

.row.inline.off {
  opacity: 0.5;
}

.error-panel {
  border-color: var(--err);
  color: var(--err);
}
</style>
