<script setup>
/**
 * Renders the score with OSMD and drives follow-along playback.
 *
 * The audio clock is the global engine (usePlayer): the same one the bottom
 * pill and the lyrics panel read from. This view no longer owns an <audio>,
 * so the music survives tab switches, and the pill's A/B chips and this
 * panel's 跟弹音源 select are two faces of the same state. What is heard cannot
 * drift from what is read: the reference clock is the very audio the pill
 * plays, and the notes come from the backend with their exact onsets.
 *
 * OSMD 2.x `load()` takes XML *content*, not a URL — the document is fetched
 * first (a known quirk of the previous iteration, kept on purpose).
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { indexNotation, paintHighlight, soundingKeys } from '../notationHighlight'
import { scoreMusicXmlUrl } from '../api'
import { usePlayer } from '../composables/usePlayer'
import PianoKeyboard from './PianoKeyboard.vue'

const props = defineProps({
  /** Backend payload of /api/score/transcribe or /api/score/arrange. */
  result: { type: Object, required: true },
  /** The track object (id/url/variants) as loaded into the global engine. */
  track: { type: Object, required: true },
})

const player = usePlayer()

const container = ref(null)
const osmd = shallowRef(null)
const loadError = ref('')
const renderNote = ref('')
const drawing = ref(false)

const playing = computed(() => player.state.playing)
const currentTime = ref(0)
const followScroll = ref(true)
const showKeyboard = ref(true)
const highlightNotation = ref(true)
const activeMidi = ref([])

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

const hasNotes = computed(() => (props.result?.score?.notes || []).length > 0)

/** True while the global engine holds this score's track — only then do we drive highlights. */
const isDriving = computed(() => player.isTrackLoaded(props.track))

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
  if (isDriving.value) {
    const time = player.getCurrentTime()
    currentTime.value = time
    if (player.state.playing) {
      updateActiveKeys(time)
      updateNotationHighlight(time)
      if (followScroll.value) scrollToCursor()
    } else if (paintedKeys.size) {
      // A "now" indicator left lit while stopped would claim notes are sounding
      // when nothing is.
      clearNotationHighlight()
      activeMidi.value = []
    }
  }
  frame = requestAnimationFrame(tick)
}

/**
 * Repaint the note-heads that are sounding.
 *
 * Driven from the same clock as the keyboard and the same note list, so the
 * staff and the keys cannot disagree about where the music is.
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
    // The payload's own URL names this score's file (cache key + version), so the
    // notation cannot be the newest score's while the audio is this one's.
    const response = await fetch(props.result?.musicxmlUrl || scoreMusicXmlUrl())
    if (!response.ok) throw new Error(`乐谱接口返回 ${response.status}`)
    const xml = await response.text()

    const { OpenSheetMusicDisplay } = await import('opensheetmusicdisplay')
    if (!osmd.value) {
      osmd.value = new OpenSheetMusicDisplay(host, {
        autoResize: false,
        drawTitle: true,
        drawMeasureNumbers: true,
        followCursor: false,
        cursorsOptions: [{ type: 0, color: '#000000', alpha: 0, follow: false }],
      })
    }
    await osmd.value.load(xml)
    osmd.value.render()
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
  if (isDriving.value) {
    player.toggle()
  } else {
    player.loadTrack(props.track, { autoplay: true })
  }
}

function seek(event) {
  const total = totalSeconds.value
  const box = event.currentTarget.getBoundingClientRect()
  const ratio = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1)
  if (!isDriving.value) {
    player.loadTrack(props.track)
    return
  }
  player.seek(ratio * total)
  currentTime.value = player.getCurrentTime()
  updateActiveKeys(currentTime.value)
  // Jumping leaves the previous bar's notes lit, which looks like the music is
  // sounding where it is not.
  clearNotationHighlight()
  updateNotationHighlight(currentTime.value)
}

function clearNotationHighlight() {
  if (paintedKeys.size && notationIndex.size) {
    paintHighlight(notationIndex, new Set(), paintedKeys)
  }
  paintedKeys = new Set()
}

watch(
  () => props.result,
  async (value) => {
    if (!value) return
    currentTime.value = 0
    activeMidi.value = []
    paintedKeys = new Set()
    notationIndex = new Map()
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
      <button class="primary" :disabled="!track?.url" @click="togglePlay">
        {{ playing && isDriving ? '⏸ 暂停' : '▶ 跟弹' }}
      </button>
      <div class="progress" @click="seek" title="点击跳转">
        <div class="progress-fill" :style="{ width: `${progress}%` }"></div>
      </div>
      <span class="time">{{ clock }}</span>
      <label class="inline">
        <input v-model="showKeyboard" type="checkbox" />钢琴键盘
      </label>
      <label class="inline" :title="notationIndex.size ? '' : '渲染后未建立高亮索引'">
        <input v-model="highlightNotation" type="checkbox" :disabled="!notationIndex.size" />乐谱高亮
      </label>
      <label class="inline">
        <input v-model="followScroll" type="checkbox" />跟随滚动
      </label>
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
      钢琴键按乐谱上每个音的确切起音时间点亮，连奏会一直亮到该音结束。播放由底部播放条统一控制，
      切到其他页签音乐不会停；进度条与底部播放条是同一个时钟。
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
  background: #fff;
  color: #09090b;
  border: none;
  border-radius: var(--pill);
  padding: 8px 20px;
  font-weight: 600;
  cursor: pointer;
}

.progress {
  flex: 1 1 220px;
  height: 8px;
  background: var(--glass-2);
  border-radius: 999px;
  overflow: hidden;
  cursor: pointer;
  min-width: 160px;
}

.progress-fill {
  height: 100%;
  background: #fff;
}

.time {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  min-width: 118px;
  font-size: 11px;
}

label.inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 13px;
}

.banner {
  padding: 8px 14px;
  border-radius: 999px;
  font-size: 13px;
  background: var(--glass-1);
  border: 1px solid var(--line-soft);
  color: var(--muted);
}

.banner.err {
  color: var(--err);
  border-color: rgba(248, 113, 113, 0.4);
}

.banner.ok {
  color: var(--ok);
}

.osmd-host {
  min-height: 340px;
  max-height: 70vh;
  overflow: auto;
  background: #fff;
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
