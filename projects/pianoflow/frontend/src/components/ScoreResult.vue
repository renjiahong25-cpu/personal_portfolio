<script setup>
/**
 * The result section shared by both score modules: the summary strip, the
 * fidelity report, the follow-along audio choice, the rendered score and the
 * downloads.
 *
 * Playback lives in the global engine (usePlayer), not here: the score's stems
 * are loaded into the bottom pill as the track's A/B variants, so the music
 * keeps playing while the user wanders to another tab, and the pill's chips
 * and this panel's chips are two faces of the same state. The stems share one
 * timeline (synthesised from the same score), so switching carries the
 * playhead over.
 */
import { computed, ref, watch } from 'vue'
import { scoreMidiUrl } from '../api'
import { usePlayer } from '../composables/usePlayer'
import ScoreView from './ScoreView.vue'

const props = defineProps({
  result: { type: Object, required: true },
  /** The source recording URL; the fallback audio for follow-along. */
  originalAudioUrl: { type: String, default: '' },
})

const player = usePlayer()

const FOLLOW_SOURCES = [{ value: 'piano', label: '生成钢琴轨' }]
const PREVIEW_SOURCES = [
  { value: 'melody', label: '仅旋律' },
  { value: 'left', label: '仅左手' },
  { value: 'original', label: '原始混音' },
]
const AUDIO_SOURCES = [...FOLLOW_SOURCES, ...PREVIEW_SOURCES]

const followAlongSource = ref('piano')
/** null = follow the 跟弹音源 selection. */
const previewSource = ref(null)

const effectiveVariant = computed(() => previewSource.value ?? followAlongSource.value)

const pianoAvailable = computed(() => Boolean(props.result?.pianoTrackUrl))
const melodyAvailable = computed(() => Boolean(props.result?.melodyTrackUrl))
const leftAvailable = computed(() => Boolean(props.result?.leftHandTrackUrl))

function urlFor(value) {
  const full = pianoAvailable.value ? props.result.pianoTrackUrl : props.originalAudioUrl
  switch (value) {
    case 'original':
      return props.originalAudioUrl
    case 'melody':
      return melodyAvailable.value ? props.result.melodyTrackUrl : full
    case 'left':
      return leftAvailable.value ? props.result.leftHandTrackUrl : full
    case 'piano':
    default:
      return full
  }
}

function sourcePlayable(value) {
  switch (value) {
    case 'melody':
      return melodyAvailable.value
    case 'left':
      return leftAvailable.value
    case 'original':
      return Boolean(props.originalAudioUrl)
    case 'piano':
    default:
      return true
  }
}

const followAlongOptions = computed(() => FOLLOW_SOURCES.filter((s) => sourcePlayable(s.value)))
const previewOptions = computed(() => PREVIEW_SOURCES.filter((s) => sourcePlayable(s.value)))

/** What the user hears right now, named in the UI so there is no mystery. */
const playingLabel = computed(() => {
  const variant = player.state.variantId || effectiveVariant.value
  return AUDIO_SOURCES.find((s) => s.value === variant)?.label || ''
})

/**
 * The track object handed to the global engine. The id names this score (its
 * cache key), which is what lets the pill, this panel and ScoreView agree on
 * "whose" playback they are looking at.
 */
const scoreTrack = computed(() => ({
  id: `score:${props.result?.cacheKey || 'latest'}`,
  title: props.result?.title || '钢琴演奏',
  artist: 'PianoFlow',
  cover: '',
  url: urlFor(effectiveVariant.value),
  variants: AUDIO_SOURCES.filter((s) => sourcePlayable(s.value)).map((s) => ({
    id: s.value,
    label: s.label,
    url: urlFor(s.value),
  })),
  lyrics: null,
}))

function togglePreview(value) {
  previewSource.value = previewSource.value === value ? null : value
}

function selectVariant(value) {
  if (PREVIEW_SOURCES.some((s) => s.value === value)) previewSource.value = value
  else {
    previewSource.value = null
    followAlongSource.value = value
  }
  player.setVariant(value)
}

// A new score replaces the loaded track (stopped, ready for 跟弹).
watch(
  () => props.result,
  () => {
    previewSource.value = null
    if (!sourcePlayable(followAlongSource.value)) followAlongSource.value = 'piano'
    player.loadTrack(scoreTrack.value)
  },
  { immediate: true },
)

// The panel's select drives the engine.
watch(effectiveVariant, (value) => {
  if (value && player.state.track?.id === scoreTrack.value.id) player.setVariant(value)
})

// The pill's chips drive the engine too; keep this panel's selection in step.
watch(
  () => player.state.variantId,
  (value) => {
    if (!value || value === effectiveVariant.value) return
    if (player.state.track?.id !== scoreTrack.value.id) return
    if (PREVIEW_SOURCES.some((s) => s.value === value)) previewSource.value = value
    else followAlongSource.value = value
  },
)

const midiUrl = computed(() => props.result?.midiUrl || scoreMidiUrl())
const report = computed(() => props.result?.fidelityReport || null)
const recomposedBars = computed(() => props.result?.quality?.recomposedBars || [])
const sectionMelodyNotes = computed(() => Number(props.result?.sectionMelody?.promotedNotes) || 0)
const sectionMelodyLabels = computed(() => props.result?.sectionMelody?.labels || [])

/** Why a requested mode is not the one playing. */
const audioFallbackNote = computed(() => {
  if (!props.result) return ''
  if (effectiveVariant.value === 'piano' && !pianoAvailable.value) {
    return `未生成钢琴轨（${props.result.piano?.reason || '缺少合成器'}），正在播放原始混音`
  }
  if (effectiveVariant.value === 'melody' && !melodyAvailable.value && pianoAvailable.value) {
    return '未生成旋律轨，正在播放完整钢琴轨'
  }
  if (effectiveVariant.value === 'left' && !leftAvailable.value && pianoAvailable.value) {
    return '未生成左手轨，正在播放完整钢琴轨'
  }
  return ''
})
</script>

<template>
  <div class="summary">
    <span>{{ result.score.measureCount }} 小节</span>
    <span>{{ result.score.noteCount }} 音</span>
    <span v-if="result.score.tiedNoteCount">{{ result.score.tiedNoteCount }} 个连线</span>
    <span>{{ result.grid.tempoBpm }} BPM</span>
    <span v-if="result.transcriber" class="transcriber-used">
      转录模型 {{ result.transcriber }}
    </span>
    <span v-if="result.key.mode">{{ result.key.mode }} {{ result.key.fifths >= 0 ? '#' : 'b' }}</span>
    <span>左右手分界 {{ result.handSplit.splitMidi }}</span>
    <span :class="{ warn: result.quality.meanScore < 80 }">
      小节质量均分 {{ result.quality.meanScore }}
    </span>
    <span v-if="recomposedBars.length">重配小节 {{ recomposedBars.join(', ') }}</span>
    <span v-if="sectionMelodyNotes" :title="`小节 ${result.sectionMelody.promotedBars.join(', ')}`">
      器乐旋律提升 {{ sectionMelodyNotes }} 音（{{ sectionMelodyLabels.join('/') }}）
    </span>
    <span v-if="result.truncated">仅分析前 {{ result.analysedSeconds }} 秒</span>
  </div>

  <div v-if="report" class="fidelity">
    <span class="fidelity-title">忠实度</span>
    <span :class="{ warn: report.retention < 0.9 }">保留 {{ (report.retention * 100).toFixed(1) }}%</span>
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
    <label class="label" for="audio-source">跟弹音源</label>
    <select id="audio-source" :value="effectiveVariant" @change="selectVariant($event.target.value)">
      <option
        v-for="source in followAlongOptions"
        :key="source.value"
        :value="source.value"
      >
        {{ source.label }}
      </option>
    </select>
    <template v-if="previewOptions.length">
      <span class="label">试听对比</span>
      <div class="preview-srcs" role="group" aria-label="试听对比">
        <button
          v-for="source in previewOptions"
          :key="source.value"
          type="button"
          class="chip"
          :class="{ active: effectiveVariant === source.value }"
          @click="togglePreview(source.value)"
        >
          {{ source.label }}
        </button>
      </div>
    </template>
    <span class="playing">正在播放：{{ playingLabel }}</span>
    <span v-if="pianoAvailable" class="badge ok">钢琴轨已生成</span>
    <span v-else class="badge off">钢琴轨未生成</span>
  </div>
  <p v-if="audioFallbackNote" class="hint warn">{{ audioFallbackNote }}</p>

  <ScoreView :result="result" :track="scoreTrack" />

  <div class="downloads">
    <a :href="midiUrl" download>下载 MIDI（双轨）</a>
  </div>
</template>

<style scoped>
.summary {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 12px;
}

.summary .warn {
  color: var(--warn);
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

.transcriber-used {
  color: var(--text);
  font-weight: 600;
}

.hint.warn {
  color: var(--warn);
  margin: 0 0 10px;
}

.follow-along {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.follow-along .label {
  color: var(--muted);
  font-size: 13px;
}

.follow-along select {
  background: var(--glass-1);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--pill);
  padding: 7px 14px;
  min-width: 140px;
}

.preview-srcs {
  display: inline-flex;
  gap: 6px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
}

.follow-along .playing {
  color: var(--text);
  font-size: 13px;
}

.badge {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: var(--pill);
  border: 1px solid var(--line);
}

.badge.ok {
  color: var(--ok);
  border-color: currentColor;
}

.badge.off {
  color: var(--muted);
}

.downloads {
  margin-top: 12px;
}

.downloads a {
  color: var(--text);
  text-decoration: none;
  font-size: 13px;
}
</style>
