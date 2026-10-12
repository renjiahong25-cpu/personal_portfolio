<script setup>
/**
 * Renders the score with OSMD and drives follow-along playback.
 *
 * Two fixes live here that are worth knowing about:
 *
 * 1. OSMD 2.x `load()` takes XML *content*, not a URL. Passing the endpoint path
 *    is accepted and produces an empty canvas, so the document is fetched first.
 * 2. The cursor is driven by the audio element's own clock, not by OSMD's
 *    follow-cursor. The reference here is the user's audio file, and the notes
 *    come from the backend with their exact onset times, so a piano key lights
 *    up when its note actually sounds.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { indexNotation, paintHighlight, soundingKeys } from '../notationHighlight'
import { scoreMusicXmlUrl } from '../api'
import PianoKeyboard from './PianoKeyboard.vue'

const props = defineProps({
  /** Backend payload of /api/score/analyse. */
  result: { type: Object, required: true },
  /** The audio the score came from; this is the playback clock. */
  audioUrl: { type: String, default: '' },
})

const container = ref(null)
const osmd = shallowRef(null)
const loadError = ref('')
const renderNote = ref('')
const drawing = ref(false)

const audio = ref(null)
const playing = ref(false)
const currentTime = ref(0)
const volume = ref(0.8)
const followScroll = ref(true)
const showKeyboard = ref(true)
const activeMidi = ref([])
/** Follow-along highlighting of the note-heads, keyed by "bar:beat". */
const highlightNotation = ref(true)

let frame = 0
let resizeObserver = null
/** bar:beat -> engraved note-heads, built once per render. */
let notationIndex = new Map()
/** The keys currently painted, so only changes are recoloured. */
let paintedKeys = new Set()

/**
 * Playable length in seconds.
 *
 * Taken from the last note's own time rather than from measureCount * barSeconds:
 * the backend's times already include the phase offset and the quantisation, so
 * recomputing a notated clock here would drift away from the audio.
 */
const totalSeconds = computed(() => {
  const notes = props.result?.score?.notes || []
  if (notes.length) {
    const last = notes[notes.length - 1]
    return last.time + last.duration
  }
  const score = props.result?.score
  if (!score?.measureCount) return 0
  return (score.measureCount * (score.beatsPerBar || 4) * 60) / (score.tempoBpm || 120)
})

const pitchRange = computed(() => props.result?.score?.pitchRange || [48, 84])

const progress = computed(() => {
  const total = totalSeconds.value
  return total > 0 ? Math.min((currentTime.value / total) * 100, 100) : 0
})

const clock = computed(() => `${currentTime.value.toFixed(1)} / ${totalSeconds.value.toFixed(1)}s`)

/** Guards against a score that rendered but carries no playable notes. */
const hasNotes = computed(() => (props.result?.score?.notes || []).length > 0)

/**
 * Which keys are sounding right now.
 *
 * A note stays lit for its full written duration, so a chord is visible as a
 * shape instead of a flicker. Checked on every animation frame, which is cheap
 * because the list is sorted and binary-searched.
 */
function updateActiveKeys(time) {
  const notes = props.result?.score?.notes || []
  const lit = []
  for (const note of notes) {
    if (note.time > time + 1e-3) break
    if (time < note.time + note.duration) lit.push(note.midi)
  }
  activeMidi.value = lit
}

function tick() {
  if (audio.value && playing.value) {
    currentTime.value = audio.value.currentTime
    updateActiveKeys(currentTime.value)
    updateNotationHighlight(currentTime.value)
    if (followScroll.value) scrollToCursor()
  }
  frame = requestAnimationFrame(tick)
}

/**
 * Repaint the note-heads that are sounding.
 *
 * Driven from the same audio clock as the keyboard, and from the same note list, so
 * the staff and the keys cannot disagree about where the music is.
 */
function updateNotationHighlight(time) {
  if (!highlightNotation.value || !notationIndex.size) return
  const notes = props.result?.score?.notes || []
  const sounding = soundingKeys(notes, time)
  if (sounding.size === paintedKeys.size) {
    let same = true
    for (const key of sounding) {
      if (!paintedKeys.has(key)) {
        same = false
        break
      }
    }
    if (same) return
  }
  paintHighlight(notationIndex, sounding, paintedKeys)
  paintedKeys = sounding
}

function scrollToCursor() {
  const element = container.value?.querySelector('.osmd-scroll-page, .osmd-canvas')
  if (!element) return
  const total = totalSeconds.value
  const ratio = total > 0 ? currentTime.value / total : 0
  element.scrollTop = element.scrollHeight * Math.min(Math.max(ratio, 0), 1)
}

async function render() {
  const host = container.value
  if (!host) return
  drawing.value = true
  loadError.value = ''
  try {
    // OSMD 2.x treats a string as XML content, not as a URL to fetch.
    const response = await fetch(scoreMusicXmlUrl())
    if (!response.ok) throw new Error(`乐谱接口返回 ${response.status}`)
    const xml = await response.text()

    const { OpenSheetMusicDisplay } = await import('opensheetmusicdisplay')
    if (!osmd.value) {
      osmd.value = new OpenSheetMusicDisplay(host, {
        autoResize: false,
        drawTitle: true,
        drawMeasureNumbers: true,
        followCursor: false,
        // A cursor has to exist for osmd.cursor to be non-null, and the note-head
        // highlight is built from it. Fully transparent, because the highlight - not
        // a rectangle - is what the player looks for.
        cursorsOptions: [{ type: 0, color: '#000000', alpha: 0, follow: false }],
        // A piano score is two staves on one system; the default engraving is
        // fine, so nothing else needs overriding.
      })
    }
    await osmd.value.load(xml)
    osmd.value.render()
    // Index only after the layout exists: the cursor walks the *rendered* score, so
    // asking before render() returns would find no note-heads at all.
    notationIndex = indexNotation(osmd.value)
    paintedKeys = new Set()
    const indexed = notationIndex.size
    renderNote.value = indexed
      ? `已渲染 ${props.result?.score?.measureCount ?? '?'} 小节，跟弹高亮就绪`
      : `已渲染 ${props.result?.score?.measureCount ?? '?'} 小节（高亮不可用）`
  } catch (error) {
    loadError.value = error?.message || '乐谱渲染失败'
  } finally {
    drawing.value = false
  }
}

function togglePlay() {
  if (!audio.value) return
  if (playing.value) {
    audio.value.pause()
    // The highlight is a "now" indicator; leaving it lit while stopped would claim
    // notes are sounding when nothing is.
    clearNotationHighlight()
  } else {
    audio.value.play().catch(() => {})
  }
}

function onEnded() {
  playing.value = false
  currentTime.value = 0
  activeMidi.value = []
  clearNotationHighlight()
}

function onVolume() {
  if (audio.value) audio.value.volume = volume.value
}

/** Put every painted note-head back to its normal colour. */
function clearNotationHighlight() {
  if (paintedKeys.size && notationIndex.size) {
    paintHighlight(notationIndex, new Set(), paintedKeys)
  }
  paintedKeys = new Set()
}

function seek(event) {
  if (!audio.value) return
  const box = event.currentTarget.getBoundingClientRect()
  const ratio = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1)
  const target = ratio * totalSeconds.value
  audio.value.currentTime = target
  currentTime.value = target
  updateActiveKeys(target)
  // Jumping leaves the previous bar's notes lit, which looks like the music is
  // sounding where it is not.
  clearNotationHighlight()
  updateNotationHighlight(target)
}

watch(
  () => props.result,
  async (value) => {
    if (!value) return
    currentTime.value = 0
    activeMidi.value = []
    // A new score invalidates the old index: the note-heads it referred to have
    // been thrown away by the re-render.
    paintedKeys = new Set()
    notationIndex = new Map()
    // Drop the previous recording. Vue only reloads <audio> when :src changes,
    // and the backend serves every output from one URL, so without an explicit
    // reset the element keeps playing the *previous* score against the new
    // notation - which is exactly the drift this component exists to prevent.
    if (audio.value) {
      audio.value.pause()
      audio.value.removeAttribute('src')
      audio.value.load()
    }
    // The container only exists once the parent has rendered the panel, so wait
    // a tick before touching it.
    await nextTick()
    await render()
  },
  { immediate: true },
)

onMounted(() => {
  frame = requestAnimationFrame(tick)
  if (container.value && 'ResizeObserver' in window) {
    resizeObserver = new ResizeObserver(() => osmd.value?.update())
    resizeObserver.observe(container.value)
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  resizeObserver?.disconnect()
})
</script>

<template>
  <div class="score-view">
    <div class="transport">
      <button class="primary" @click="togglePlay">
        {{ playing ? '⏸ 暂停' : '▶ 跟弹' }}
      </button>
      <div class="progress" @click="seek" title="点击跳转">
        <div class="progress-fill" :style="{ width: `${progress}%` }"></div>
      </div>
      <span class="time">{{ clock }}</span>
      <label class="inline">
        音量<input v-model.number="volume" type="range" min="0" max="1" step="0.05" @input="onVolume" />
      </label>
      <label class="inline">
        <input v-model="showKeyboard" type="checkbox" />钢琴键盘
      </label>
      <label class="inline" :title="notationIndex.size ? '' : '渲染后未建立高亮索引'">
        <input v-model="highlightNotation" type="checkbox" :disabled="!notationIndex.size" />乐谱高亮
      </label>
      <label class="inline">
        <input v-model="followScroll" type="checkbox" />跟随滚动
      </label>
      <audio
        v-if="audioUrl"
        ref="audio"
        :src="audioUrl"
        preload="metadata"
        @play="playing = true"
        @pause="playing = false"
        @ended="onEnded"
      ></audio>
    </div>

    <PianoKeyboard v-if="showKeyboard" :active-midi="activeMidi" :range="pitchRange" />

    <div v-if="loadError" class="banner err">渲染失败：{{ loadError }}</div>
    <div v-else-if="drawing" class="banner">正在渲染乐谱…</div>
    <div v-else-if="renderNote" class="banner ok">{{ renderNote }}</div>
    <div v-else-if="!hasNotes" class="banner err">
      乐谱里没有音符，无法跟弹。请在「编排方式」里换一个音源再试。
    </div>

    <div ref="container" class="osmd-host" :class="{ dimmed: drawing || loadError }"></div>

    <p class="hint">
      钢琴键按乐谱上每个音的确切起音时间点亮，连奏会一直亮到该音结束。播放的时钟是你自己的音频，
      请从开头播放，否则音画会不同步。
    </p>
  </div>
</template>

<style scoped>
.score-view {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.transport {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

button.primary {
  background: var(--accent);
  color: #04121f;
  border: none;
  border-radius: 8px;
  padding: 7px 16px;
  font-weight: 600;
  cursor: pointer;
}

.progress {
  flex: 1 1 220px;
  height: 8px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 999px;
  overflow: hidden;
  cursor: pointer;
  min-width: 160px;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
}

.time {
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  min-width: 108px;
}

label.inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 13px;
}

.banner {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  color: var(--muted);
}

.banner.err {
  color: var(--err);
  border-color: var(--err);
}

.banner.ok {
  color: var(--ok);
}

.osmd-host {
  min-height: 340px;
  max-height: 70vh;
  overflow: auto;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px;
}

.osmd-host.dimmed {
  opacity: 0.45;
}

.hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
</style>
