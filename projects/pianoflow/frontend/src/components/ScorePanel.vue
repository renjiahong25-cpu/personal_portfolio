<script setup>
/**
 * The score tab: pick a source (cached song or a local file), choose how it
 * should be arranged, generate, then read the result.
 *
 * The options are fetched from the backend rather than hard-coded, so the
 * difficulty labels the user reads are the ones the server actually implements.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  analyseScore,
  cacheAudioUrl,
  fetchCacheStatus,
  fetchScoreOptions,
  formatDuration,
  scoreMidiUrl,
  uploadAndAnalyse,
} from '../api'
import ScoreView from './ScoreView.vue'

const options = ref(null)
const tracks = ref([])
const selectedKey = ref('')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const fileInput = ref(null)

const fidelity = ref('transcribed')
const fillChords = ref(false)
const difficulty = ref(3)
const pattern = ref('')
const previewSeconds = ref(30)
/**
 * Which model does the transcription.
 *
 * Defaults to empty rather than to a name, so the backend's own default applies until
 * the user picks. That matters because the default is a server-side setting: someone who
 * sets PIANOFLOW_TRANSCRIBER gets their choice, and a hard-coded 'basic_pitch' here would
 * override it silently on every request.
 */
const transcriber = ref('')

const selectedTrack = computed(() => tracks.value.find((t) => t.key === selectedKey.value))

const canGenerate = computed(() => Boolean(selectedKey.value) && !busy.value)

/**
 * Which audio follow-along plays.
 *
 * The default is the generated piano track rather than the source recording, and
 * that is the whole point of it: the track is synthesised from score.notes, the
 * same list that places the notes on the staff and lights the keys, so what is
 * heard cannot drift from what is read. The original mix is kept as a fallback
 * because the transcription is never perfect and the user needs to hear the
 * original to judge it.
 */
const AUDIO_SOURCES = [
  { value: 'piano', label: '生成钢琴轨' },
  { value: 'pianoVocals', label: '钢琴 + 人声' },
  { value: 'original', label: '原始混音' },
]

const audioSource = ref('piano')

/** The source recording URL, needed by every mode. */
const originalAudioUrl = ref('')
/** True only when the backend actually produced a track. */
const pianoAvailable = computed(() => Boolean(result.value?.pianoTrackUrl))
const vocalsAvailable = computed(() => Boolean(result.value?.vocalsTrackUrl))

/**
 * What ScoreView actually plays.
 *
 * A mode whose audio does not exist falls through to the piano track and then to
 * the original, rather than handing the player an empty src and silently
 * producing no sound.
 */
const audioUrl = computed(() => {
  switch (audioSource.value) {
    case 'original':
      return originalAudioUrl.value
    case 'pianoVocals':
      if (vocalsAvailable.value) return result.value.vocalsTrackUrl
      if (pianoAvailable.value) return result.value.pianoTrackUrl
      return originalAudioUrl.value
    case 'piano':
    default:
      if (pianoAvailable.value) return result.value.pianoTrackUrl
      return originalAudioUrl.value
  }
})

/** What the user hears right now, named in the UI so there is no mystery. */
const audioSourceLabel = computed(() => {
  if (audioUrl.value === originalAudioUrl.value && !pianoAvailable.value) return AUDIO_SOURCES[2].label
  if (audioSource.value === 'pianoVocals' && vocalsAvailable.value) return AUDIO_SOURCES[1].label
  if (pianoAvailable.value && audioUrl.value === result.value?.pianoTrackUrl) return AUDIO_SOURCES[0].label
  return AUDIO_SOURCES[2].label
})

/** Why a requested mode is not the one playing. */
const audioFallbackNote = computed(() => {
  if (!result.value) return ''
  if (audioSource.value === 'piano' && !pianoAvailable.value) {
    return `未生成钢琴轨（${result.value.piano?.reason || '缺少合成器'}），正在播放原始混音`
  }
  if (audioSource.value === 'pianoVocals' && !vocalsAvailable.value) {
    return `暂无人声分离结果（${result.value.vocals?.reason || '需要 Demucs 模型'}），已回退`
  }
  return ''
})

/** Modes worth offering: never show one that cannot be played. */
const audioSourceOptions = computed(() =>
  AUDIO_SOURCES.filter((s) => {
    if (s.value === 'piano') return true
    if (s.value === 'pianoVocals') return vocalsAvailable.value
    return Boolean(originalAudioUrl.value)
  })
)

const difficultyEntries = computed(() => {
  const map = options.value?.difficulties || {}
  return Object.entries(map).map(([level, hint]) => ({ level, hint }))
})

const fidelityLabels = {
  'as-played': '原样转录（仍会按难度削减）',
  hybrid: '混合（只重配质量差的小节）',
  arranged: '全部重配',
  transcribed: '钢琴原谱（忠实转录，不改难度）',
}

const patternLabels = {
  '': '自动判断',
  root_only: '单音根音',
  root_octave: '根音 + 八度',
  block_chord: '柱式和弦',
  broken_chord: '分解和弦',
  alberti_bass: '阿尔贝蒂低音',
}

const texture = ref('')
const texturePlan = ref('')

const textureLabels = {
  '': '使用织体系统',
  block: '柱式',
  perc332: '打击感332',
  rhythm: '节奏动机',
  half: '半分解',
  bass: '低音线条',
  arplong: '全分解长线条副旋律',
  arpfull: '全分解和弦琶音',
  riff: 'riff乐段动机',
}

/**
 * The models on offer, from the backend so there is one source of truth.
 *
 * A model that is not installed is shown but disabled rather than hidden, because
 * "transkun is not installed, here is how" is more use than an absent option. The
 * explanation comes from the backend's own hint text - the UI has no independent opinion
 * about how long a model takes or how its note lengths behave.
 */
const transcriberEntries = computed(() => {
  const names = options.value?.transcribers || []
  const hints = options.value?.transcriberHints || {}
  return names.map((name) => ({
    name,
    hint: hints[name] || '',
    available: name === 'basic_pitch' || options.value?.transkunAvailable === true,
  }))
})

/**
 * Why a model cannot be picked, or '' when it can.
 *
 * phrased as a sentence rather than a bare flag so the disabled control explains itself.
 */
const transcriberUnavailableNote = computed(() => {
  const entry = transcriberEntries.value.find((e) => e.name === transcriber.value)
  if (entry && !entry.available) {
    return `「${entry.name}」未安装：需要独立环境 pip install transkun，并设 PIANOFLOW_TRANSKUN_PYTHON`
  }
  return ''
})

/** Label for the current choice, including the backend default when none was made. */
const transcriberLabel = computed(() => {
  const entries = transcriberEntries.value
  const fallback = options.value?.defaultTranscriber || 'basic_pitch'
  const entry = entries.find((e) => e.name === (transcriber.value || fallback))
  if (!entry) return fallback
  const chosen = transcriber.value || `（默认 ${fallback}）`
  return `${chosen} · ${entry.hint}`
})

/** What the faithful mode did to the transcription, for the honesty panel. */
const report = computed(() => result.value?.fidelityReport || null)

/** Bars the backend chose to rewrite, for the honesty panel. */
const recomposedBars = computed(() => result.value?.quality?.recomposedBars || [])

async function loadOptions() {
  try {
    options.value = await fetchScoreOptions()
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
  audioSource.value = 'piano'
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
    const payload = await analyseScore({
      cacheKey: selectedKey.value,
      fidelity: fidelity.value,
      fillChords: fillChords.value,
      difficulty: difficulty.value,
      pattern: pattern.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
      texture: texture.value,
      texturePlan: texturePlan.value,
    })
    applyPayload(payload)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function onFilePicked(event) {
  const file = event.target.files?.[0]
  if (!file) return
  busy.value = true
  reset()
  try {
    const payload = await uploadAndAnalyse(file, {
      fidelity: fidelity.value,
      fillChords: fillChords.value,
      difficulty: difficulty.value,
      pattern: pattern.value,
      previewSeconds: Number(previewSeconds.value) || 0,
      transcriber: transcriber.value,
      texture: texture.value,
      texturePlan: texturePlan.value,
    })
    applyPayload(payload)
    await loadTracks()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

watch(selectedKey, () => {
  // Switching the source must not leave the previous score on screen.
  reset()
})

onMounted(async () => {
  await Promise.all([loadOptions(), loadTracks()])
})
</script>

<template>
  <div class="score-page">
    <section class="panel">
      <h2>1 · 选择音源</h2>
      <div class="row">
        <select v-model="selectedKey">
          <option value="">— 选择本地缓存中的曲目 —</option>
          <option v-for="t in tracks" :key="t.key" :value="t.key">
            {{ t.title }} · {{ formatDuration(t.duration) }}
          </option>
        </select>
        <button :disabled="!canGenerate" @click="generate">
          {{ busy ? '分析中…' : '生成乐谱' }}
        </button>
      </div>

      <div class="row upload-row">
        <span class="label">或上传本地音频</span>
        <input
          ref="fileInput"
          type="file"
          accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac,.wma"
          :disabled="busy"
          @change="onFilePicked"
        />
      </div>
      <p class="hint">
        支持 wav / mp3 / m4a / flac / ogg / opus / aac / wma，最大 100 MB、1 小时。上传后会先转成单声道
        22050 Hz，再走与缓存曲目完全相同的那条分析链路。
      </p>
    </section>

    <section class="panel">
      <h2>2 · 转录模型</h2>
      <div class="grid">
        <label>
          模型
          <select v-model="transcriber">
            <option value="">
              默认（{{ options?.defaultTranscriber || 'basic_pitch' }}）
            </option>
            <option
              v-for="entry in transcriberEntries"
              :key="entry.name"
              :value="entry.name"
              :disabled="!entry.available"
            >
              {{ entry.name }}{{ entry.available ? '' : '（未安装）' }}
            </option>
          </select>
        </label>
      </div>
      <p class="hint">{{ transcriberLabel }}</p>
      <p v-if="transcriberUnavailableNote" class="hint warn">
        {{ transcriberUnavailableNote }}
      </p>
      <p class="hint">
        两个模型对同一段音频的音符时值判断差别很大：实测 20 秒片段，
        <code>basic_pitch</code> 用 2.0 秒、时值中位 0.75 拍；
        <code>transkun</code> 用 20.9 秒、时值中位 0.50 拍，且音符更短更碎。
        <code>transkun</code> 的时值来自回归头、不按踏板撑长；
        <code>basic_pitch</code> 完全没有 offset 头，时值是包络衰减推测出来的。
        速度差约十倍，长曲子建议先用「分析长度」预览一段。
      </p>
    </section>

    <section class="panel">
      <h2>3 · 编排方式</h2>
      <div class="grid">
        <label>
          保真度
          <select v-model="fidelity">
            <option v-for="(label, value) in fidelityLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
        <label>
          左手织体
          <select v-model="pattern" :disabled="fidelity === 'transcribed' || texture">
            <option v-for="(label, value) in patternLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </label>
        <label>
          伴奏织体
          <select v-model="texture" :disabled="fidelity === 'transcribed'">
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

      <p class="hint">
        「钢琴原谱」把转录结果原样记谱，只在和弦一只手够不到时降八度，不按难度削减音符 ——
        适合音源本身就是钢琴的时候。留影框可把单个和弦音补成三和弦。
      </p>
      <p v-if="texture" class="hint">
        伴奏织体已启用：左手将使用「{{ textureLabels[texture] }}」织体生成，而非传统的 pattern 系统。
      </p>

      <div class="levels">
        <button
          v-for="entry in difficultyEntries"
          :key="entry.level"
          :class="{ active: difficulty === Number(entry.level) }"
          :disabled="fidelity === 'transcribed'"
          :title="fidelity === 'transcribed' ? '钢琴原谱不按难度改变音符' : entry.hint"
          @click="difficulty = Number(entry.level)"
        >
          L{{ entry.level }}
        </button>
      </div>

      <label class="row inline">
        <input v-model="fillChords" type="checkbox" />
        补齐和弦：把单个和弦音补成三和弦（会增加音符数）
      </label>
      <p class="hint">{{ difficultyEntries.find((d) => d.level === String(difficulty))?.hint }}</p>
      <p class="hint">
        「混合」只重写质量不合格的小节；「全部重配」会按和弦重写所有小节的左手，但右手旋律始终保持转录原样。
      </p>
      <p v-if="texture" class="hint">
        织体系统提供 8 种伴奏纹理：柱式、打击感332、节奏动机、半分解、低音线条、全分解长线条副旋律、全分解和弦琶音、riff乐段动机。
      </p>
    </section>

    <div v-if="error" class="panel error-panel">{{ error }}</div>

    <section v-if="result" class="panel">
      <h2>4 · 乐谱</h2>
      <div class="summary">
        <span>{{ result.score.measureCount }} 小节</span>
        <span>{{ result.score.noteCount }} 音</span>
        <span v-if="result.score.tiedNoteCount">{{ result.score.tiedNoteCount }} 个连线</span>
        <span>{{ result.grid.tempoBpm }} BPM</span>
        <!--
          Which model produced these notes. Shown because the two disagree about note
          length by a large enough margin that a score cannot be judged without knowing
          which reading of the recording it is a transcription of.
        -->
        <span v-if="result.transcriber" class="transcriber-used">
          转录模型 {{ result.transcriber }}
        </span>
        <span v-if="result.key.mode">{{ result.key.mode }} {{ result.key.fifths >= 0 ? '#' : 'b' }}</span>
        <span>左右手分界 {{ result.handSplit.splitMidi }}</span>
        <span :class="{ warn: result.quality.meanScore < 80 }">
          小节质量均分 {{ result.quality.meanScore }}
        </span>
        <span v-if="recomposedBars.length">重配小节 {{ recomposedBars.join(', ') }}</span>
        <span v-if="result.truncated">仅分析前 {{ result.analysedSeconds }} 秒</span>
      </div>

      <div v-if="report" class="fidelity">
        <span class="fidelity-title">忠实度</span>
        <span :class="{ warn: report.retention < 0.9 }">
          保留 {{ (report.retention * 100).toFixed(1) }}%
        </span>
        <span>补充 {{ report.added }}</span>
        <span :class="{ warn: report.removed > 0 }">删除 {{ report.removed }}</span>
        <span>降八度 {{ report.octaveFolded }}</span>
        <span v-if="report.groupsReduced">够不到的和弦 {{ report.groupsReduced }}</span>
        <span v-if="report.rootChanged" class="bad">根音被改动 {{ report.rootChanged }}</span>
        <span v-if="report.melodyRemoved" class="bad">旋律被删除 {{ report.melodyRemoved }}</span>
        <span v-if="report.lowConfidenceBars?.length" class="warn">
          和弦识别不确定的小节：{{ report.lowConfidenceBars.join(', ') }}
        </span>
      </div>

      <div class="follow-along">
        <label class="label" for="audio-source">跟弹音频</label>
        <select id="audio-source" v-model="audioSource">
          <option v-for="source in audioSourceOptions" :key="source.value" :value="source.value">
            {{ source.label }}
          </option>
        </select>
        <span class="playing">正在播放：{{ audioSourceLabel }}</span>
        <span v-if="pianoAvailable" class="badge ok">钢琴轨已生成</span>
        <span v-else class="badge off">钢琴轨未生成</span>
      </div>
      <p v-if="audioFallbackNote" class="hint warn">{{ audioFallbackNote }}</p>

      <ScoreView :result="result" :audio-url="audioUrl" />

      <div class="downloads">
        <a :href="scoreMidiUrl()" download>下载 MIDI（双轨）</a>
      </div>
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

.row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.upload-row {
  margin-top: 10px;
}

.label {
  color: var(--muted);
  font-size: 13px;
}

select,
input[type='file'] {
  background: var(--bg-soft);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 10px;
  min-width: 220px;
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

.levels {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.levels button {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  color: var(--muted);
  border-radius: 8px;
  padding: 6px 18px;
  cursor: pointer;
}

.levels button.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.hint {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.summary {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 12px;
}

.fidelity {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  align-items: center;
  color: var(--muted);
  font-size: 12px;
  margin: -4px 0 12px;
}

.fidelity-title {
  color: var(--text);
}

.fidelity .warn {
  color: var(--warn);
}

.fidelity .bad {
  color: var(--err);
  font-weight: 600;
}

.summary .warn {
  color: var(--warn);
}

/*
  The model that produced the notes, in the summary strip. Deliberately in the text colour
  rather than muted: it is provenance for the whole score below it, not a footnote, and the
  two models disagree enough that judging the note lengths needs it.
*/
.transcriber-used {
  color: var(--text);
  font-weight: 600;
}

.error-panel {
  border-color: var(--err);
  color: var(--err);
}

.follow-along {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.follow-along select {
  min-width: 140px;
}

.follow-along .playing {
  color: var(--text);
  font-size: 13px;
}

.badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
}

.badge.ok {
  color: var(--ok, var(--accent));
  border-color: currentColor;
}

.badge.off {
  color: var(--muted);
}

.hint.warn {
  color: var(--warn);
  margin: 0 0 10px;
}

.downloads {
  margin-top: 12px;
}

.downloads a {
  color: var(--accent);
  text-decoration: none;
  font-size: 13px;
}
</style>
