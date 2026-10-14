/**
 * The one and only audio clock for the whole app.
 *
 * Module scope (like useDownloads): switching tabs destroys the views, but the
 * player must survive — that is the whole point of integrating it. Every view
 * that plays anything (search results, cache entries, the score's three stems)
 * loads a track into this engine instead of owning its own <audio>.
 *
 * A track is:
 *   { id, title, artist, cover, url,
 *     variants: [{ id, label, url }]?,   // A/B stems on one timeline
 *     lyrics: { source, sourceId }? }
 *
 * All variants share one timeline (the stems are synthesised from the same
 * score), so switching variants carries the playhead over.
 */

import { reactive } from 'vue'
import { fetchLyrics } from '../api'
import { normalizeLyrics } from './useLyricsData'

const state = reactive({
  playing: false,
  currentTime: 0,
  duration: 0,
  volume: 0.8,
  track: null,
  variantId: null,
  showLyrics: false,
  /** Normalised word-level lines for the current track (or null). */
  lyrics: null,
  /** The fullscreen immersive lyrics stage. */
  lyricsFullscreen: false,
})

let audio = null
let raf = 0
let lyricsToken = 0

function ensureAudio() {
  if (audio) return audio
  audio = new Audio()
  audio.preload = 'metadata'
  audio.volume = state.volume
  audio.addEventListener('play', () => {
    state.playing = true
  })
  audio.addEventListener('pause', () => {
    state.playing = false
  })
  audio.addEventListener('ended', () => {
    state.playing = false
    state.currentTime = 0
  })
  audio.addEventListener('loadedmetadata', () => {
    state.duration = Number.isFinite(audio.duration) ? audio.duration : 0
  })
  audio.addEventListener('error', () => {
    state.playing = false
  })
  tick()
  return audio
}

function tick() {
  if (audio && !audio.paused) state.currentTime = audio.currentTime
  raf = requestAnimationFrame(tick)
}

function sameUrl(a, b) {
  try {
    return new URL(a, window.location.href).href === new URL(b, window.location.href).href
  } catch {
    return false
  }
}

export function usePlayer() {
  /**
   * Load a track into the engine.
   *
   * @param {object|null} track  null clears the engine.
   * @param {object} opts { autoplay, carry } — carry keeps the playhead when
   *   the url changes within the same track (A/B stem switches).
   */
  async function loadLyrics(track) {
    const token = ++lyricsToken
    state.lyrics = null
    if (!track?.lyrics?.source || !track.lyrics.sourceId) return
    try {
      const payload = await fetchLyrics(track.lyrics.source, track.lyrics.sourceId)
      if (token !== lyricsToken) return
      state.lyrics = normalizeLyrics(payload)
    } catch {
      // Lyrics are decoration: a failed fetch must never block playback.
      if (token === lyricsToken) state.lyrics = null
    }
  }

  function loadTrack(track, { autoplay = false, carry = false } = {}) {
    const el = ensureAudio()
    const previousId = state.track?.id
    state.track = track
    state.variantId = null
    if (!track || !track.url) {
      if (el.src) el.pause()
      state.currentTime = 0
      state.lyrics = null
      return
    }
    loadLyrics(track)
    if (sameUrl(el.src, track.url)) {
      if (autoplay) el.play().catch(() => {})
      return
    }
    if (carry && previousId === track.id && el.currentTime > 0.05) {
      const saved = el.currentTime
      const resume = () => {
        el.removeEventListener('loadedmetadata', resume)
        if (Number.isFinite(el.duration) && el.duration > 0) {
          el.currentTime = Math.min(saved, el.duration - 0.05)
        }
        state.currentTime = el.currentTime
        if (autoplay) el.play().catch(() => {})
      }
      el.pause()
      el.src = track.url
      el.load()
      el.addEventListener('loadedmetadata', resume)
    } else {
      el.src = track.url
      el.load()
      state.currentTime = 0
      if (autoplay) el.play().catch(() => {})
    }
  }

  /** Switch the A/B variant of the loaded track, carrying the playhead. */
  function setVariant(variantId, { autoplay = false } = {}) {
    const track = state.track
    if (!track?.variants?.length) return
    const variant = track.variants.find((v) => v.id === variantId)
    if (!variant || !variant.url) return
    state.variantId = variantId
    loadTrack({ ...track, url: variant.url }, { autoplay: autoplay || state.playing, carry: true })
  }

  function play() {
    ensureAudio().play().catch(() => {})
  }

  function pause() {
    audio?.pause()
  }

  function toggle() {
    if (state.playing) pause()
    else play()
  }

  function seek(seconds) {
    const el = ensureAudio()
    if (!el.src) return
    el.currentTime = Math.max(0, Math.min(seconds, el.duration || seconds))
    state.currentTime = el.currentTime
  }

  function setVolume(value) {
    state.volume = value
    if (audio) audio.volume = value
  }

  /** The engine's own position — views drive highlights from this, not from a copy. */
  function getCurrentTime() {
    return audio ? audio.currentTime : 0
  }

  function isTrackLoaded(track) {
    return Boolean(state.track && track && state.track.id === track.id)
  }

  /** The shared <audio> element — the fullscreen stage taps it for level data. */
  function getAudioEl() {
    return audio
  }

  function openLyrics() {
    if (state.track) state.lyricsFullscreen = true
  }

  function closeLyrics() {
    state.lyricsFullscreen = false
  }

  return {
    state,
    loadTrack,
    setVariant,
    play,
    pause,
    toggle,
    seek,
    setVolume,
    getCurrentTime,
    isTrackLoaded,
    getAudioEl,
    openLyrics,
    closeLyrics,
  }
}

/* --- LRC parsing -------------------------------------------------------- */

const LRC_TIME = /\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]/g

export function parseLrc(text) {
  const lines = []
  for (const raw of String(text || '').split(/\r?\n/)) {
    const times = [...raw.matchAll(LRC_TIME)]
    if (!times.length) continue
    const content = raw.replace(/\[[^\]]*\]/g, '').trim()
    if (!content) continue
    for (const m of times) {
      const [, mm, ss, ms] = m
      const time = parseInt(mm, 10) * 60 + parseInt(ss, 10) + (ms ? parseInt(ms, 10) / 10 ** ms.length : 0)
      lines.push({ time, content })
    }
  }
  return lines.sort((a, b) => a.time - b.time)
}

/** Binary search: the last line whose timestamp is at or before `time`. */
export function activeLineIndex(lines, time) {
  let lo = 0
  let hi = lines.length - 1
  let idx = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (lines[mid].time <= time + 0.25) {
      idx = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return idx
}
