<script setup>
/**
 * The fullscreen immersive lyrics stage — the reference's playback view,
 * ported: only the active line is rendered (not a scrolling list), each word
 * is a two-layer span (a glow layer sweeping per-character drop-shadows on a
 * CSS clock, a body layer that pops with a spring), words carry pseudo-random
 * offsets seeded by the line start, the line breathes on a 7s cycle, and the
 * ground is a slow mesh of six cover-extracted colours that flexes with the
 * audio level.
 *
 * Chrome disappears after 3s of stillness; the top-left hotspot and Escape
 * close the stage.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { formatDuration } from '../api'
import { usePlayer } from '../composables/usePlayer'
import { extractPalette, fallbackPalette, hexA } from '../composables/useColorExtract'
import { findActiveLine } from '../composables/useLyricsData'

const player = usePlayer()
const root = ref(null)
const canvas = ref(null)

const lines = computed(() => player.state.lyrics?.lines || [])
const palette = ref(fallbackPalette('pianoflow'))
const glowColor = computed(() => palette.value[0] || '#df543d')

watch(
  () => player.state.track?.id,
  (id) => {
    if (!id) return
    const track = player.state.track
    // track.cover is already the same-origin proxy — wrapping it again breaks it
    const url = track?.cover || ''
    extractPalette(url, id).then((p) => {
      palette.value = p
      initBlobs(id)
    })
  },
  { immediate: true },
)

/* --- active line + per-word states, driven by one rAF clock -------------- */

const active = ref(-1)
const exiting = ref(-1)
const wordStates = ref([])
let raf = 0
let exitTimer = 0

function tick() {
  const t = player.getCurrentTime()
  const idx = lines.value.length ? findActiveLine(lines.value, t) : -1
  if (idx !== active.value) {
    if (active.value >= 0) {
      exiting.value = active.value
      clearTimeout(exitTimer)
      exitTimer = setTimeout(() => (exiting.value = -1), 340)
    }
    active.value = idx
  }
  const line = lines.value[idx]
  if (line?.words) {
    const next = line.words.map((w) => (t < w.start ? 'waiting' : t <= w.end ? 'active' : 'passed'))
    if (next.join('|') !== wordStates.value.join('|')) wordStates.value = next
  } else {
    wordStates.value = []
  }
  drawBg()
  raf = requestAnimationFrame(tick)
}

/* --- the mesh ground: six cover colours as drifting radial blobs --------- */

let blobs = []
let seedN = 1

function frac(x) {
  return x - Math.floor(x)
}

function initBlobs(seed) {
  const h = seed.split('').reduce((s, c) => s + c.codePointAt(0), 0)
  seedN = h
  blobs = palette.value.map((color, i) => ({
    color,
    bx: 0.12 + 0.76 * frac(Math.sin(i * 12.9898 + h * 0.001) * 43758.5453),
    by: 0.12 + 0.76 * frac(Math.sin(i * 78.233 + h * 0.0013) * 43758.5453),
    r: 0.3 + 0.28 * frac(Math.sin(i * 3.7 + 1.1) * 1000),
    ph: i * 1.13,
    sp: 0.04 + 0.05 * frac(Math.sin(i * 7.7 + 0.3) * 1000),
  }))
}

/* --- audio levels: one MediaElementSource + analyser on the shared audio - */

let actx = null
let analyser = null
let freq = null

function ensureAnalyser() {
  try {
    const el = player.getAudioEl()
    if (!el || actx) return
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return
    actx = new AC()
    const src = actx.createMediaElementSource(el)
    analyser = actx.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.82
    src.connect(analyser)
    analyser.connect(actx.destination)
    freq = new Uint8Array(analyser.frequencyBinCount)
  } catch {
    actx = null
    analyser = null
  }
}

function audioLevel() {
  if (analyser && actx && actx.state === 'running') {
    analyser.getByteFrequencyData(freq)
    let bass = 0
    for (let i = 1; i < 5; i++) bass += freq[i]
    let mid = 0
    for (let i = 5; i < 40; i++) mid += freq[i]
    let power = 0
    for (let i = 0; i < freq.length; i++) power += freq[i]
    return { bass: bass / (4 * 255), mid: mid / (35 * 255), power: power / (freq.length * 255) }
  }
  const s = Math.sin(Date.now() / 2000)
  return { bass: 0, mid: 0.3 + 0.15 * s, power: 0.18 + 0.08 * s }
}

function drawBg() {
  const c = canvas.value
  if (!c) return
  const w = c.width
  const h = c.height
  if (!w || !h) return
  const ctx = c.getContext('2d')
  const lvl = audioLevel()
  const time = performance.now() / 1000

  ctx.globalCompositeOperation = 'source-over'
  ctx.filter = `saturate(${(1 + lvl.mid * 0.3).toFixed(3)}) brightness(${(0.9 + lvl.power * 0.18).toFixed(3)})`
  ctx.fillStyle = '#0b0a09'
  ctx.fillRect(0, 0, w, h)
  ctx.filter = 'none'
  ctx.globalCompositeOperation = 'screen'

  const scale = 1.02 + lvl.power * 0.04
  for (const b of blobs) {
    const x = (b.bx + Math.sin(time * b.sp + b.ph) * 0.055) * w
    const y = (b.by + Math.cos(time * b.sp * 0.8 + b.ph) * 0.05) * h
    const r = b.r * Math.max(w, h) * scale * (1 + lvl.bass * 0.05)
    const g = ctx.createRadialGradient(x, y, 0, x, y, r)
    g.addColorStop(0, hexA(b.color, 0.42 + lvl.mid * 0.16))
    g.addColorStop(1, hexA(b.color, 0))
    ctx.fillStyle = g
    ctx.fillRect(x - r, y - r, r * 2, r * 2)
  }

  ctx.globalCompositeOperation = 'source-over'
  const shade = ctx.createLinearGradient(0, h * 0.55, 0, h)
  shade.addColorStop(0, 'rgba(8,7,6,0)')
  shade.addColorStop(1, 'rgba(8,7,6,0.6)')
  ctx.fillStyle = shade
  ctx.fillRect(0, 0, w, h)
}

function resize() {
  const c = canvas.value
  if (!c) return
  c.width = Math.min(window.innerWidth, 1920)
  c.height = Math.min(window.innerHeight, 1080)
}

/* --- invisible chrome: 3s idle hides the cursor (the reference's way) ---- */

const idle = ref(false)
let idleTimer = 0

function wake() {
  idle.value = false
  clearTimeout(idleTimer)
  idleTimer = setTimeout(() => (idle.value = true), 3000)
}

/* --- pseudo-random per-word layout (the reference's sin seed) ------------ */

function wordVars(line, k) {
  const seed = line.start + k * 0.77
  const rnd = (o) => frac(Math.sin(seed + o) * 10000)
  return {
    '--wx': `${((rnd(1) - 0.5) * 40).toFixed(1)}px`,
    '--wy': `${((rnd(2) - 0.5) * 40).toFixed(1)}px`,
    '--wrot': `${((rnd(3) - 0.5) * 10).toFixed(2)}deg`,
    '--wsc': (1.1 + rnd(4) * 0.2).toFixed(3),
    '--pr': `${((rnd(5) - 0.5) * 14).toFixed(2)}deg`,
  }
}

function charVars(line, w, ci) {
  const chars = [...w.text]
  const n = Math.max(1, chars.length)
  const span = (w.end - w.start) / n
  const cStart = w.start + span * ci
  return {
    '--gdel': `${Math.max(0, cStart - line.start).toFixed(3)}s`,
    '--gdur': `${(span * 6).toFixed(3)}s`,
  }
}

const activeLine = computed(() => lines.value[active.value] || null)
const exitLine = computed(() => lines.value[exiting.value] || null)
const nextLine = computed(() => lines.value[active.value + 1] || null)
const track = computed(() => player.state.track)

function close() {
  player.closeLyrics()
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

onMounted(() => {
  ensureAnalyser()
  actx?.resume?.()
  resize()
  window.addEventListener('resize', resize)
  window.addEventListener('mousemove', wake)
  window.addEventListener('keydown', onKey)
  wake()
  raf = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  clearTimeout(exitTimer)
  clearTimeout(idleTimer)
  window.removeEventListener('resize', resize)
  window.removeEventListener('mousemove', wake)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="lyrics-fs" :class="{ idle }" @click="close">
    <canvas ref="canvas" class="lyrics-fs-bg"></canvas>
    <div class="lyrics-fs-vignette"></div>

    <div class="lyr-backspot" @click.stop>
      <button class="lyr-back" title="返回" @click="close">←</button>
    </div>

    <div class="lyr-track" v-if="track">
      <span class="t">{{ track.title }}</span>
      <span class="a" v-if="track.artist">{{ track.artist }}</span>
    </div>

    <div class="lyr-stage" v-if="exitLine" :key="'x' + exitLine.start" aria-hidden="true">
      <div class="lyr-line exit">
        <span class="lyr-plain">{{ exitLine.text }}</span>
      </div>
    </div>

    <div class="lyr-stage" v-if="activeLine" :key="activeLine.start">
      <div class="lyr-line">
        <template v-if="activeLine.words">
          <span
            v-for="(w, k) in activeLine.words"
            :key="k"
            class="lyr-word"
            :class="wordStates[k] || 'waiting'"
            :style="[wordVars(activeLine, k), { marginRight: k < activeLine.words.length - 1 ? '0.26em' : 0 }]"
          >
            <span class="lyr-glow" aria-hidden="true">
              <span
                v-for="(ch, ci) in [...w.text]"
                :key="ci"
                class="gl-ch"
                :style="charVars(activeLine, w, ci)"
                >{{ ch }}</span
              >
            </span>
            <span class="lyr-body">{{ w.text }}</span>
          </span>
        </template>
        <span v-else class="lyr-plain">{{ activeLine.text }}</span>
      </div>
    </div>

    <div v-else-if="!lines.length" class="lyr-empty">
      <p>{{ track?.title || '未在播放' }}</p>
      <span class="sub">这首歌没有可用的歌词</span>
    </div>

    <div v-else class="lyr-empty">
      <p>{{ track?.title }}</p>
      <span class="sub">{{ player.state.playing ? formatDuration(player.getCurrentTime()) : '按播放开始' }}</span>
    </div>

    <div class="lyr-next" v-if="nextLine">{{ nextLine.text }}</div>
  </div>
</template>
