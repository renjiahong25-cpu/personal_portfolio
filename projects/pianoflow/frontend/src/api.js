/**
 * Thin API client. All requests are same-origin: in development Vite proxies
 * /api to Tornado, in production Tornado serves the built assets itself.
 */

/**
 * Every request is bounded by a timeout. Without one, a stalled request leaves
 * the UI waiting forever and the page looks frozen — which is exactly what a
 * saturated connection pool used to cause.
 */

async function request(path, options = {}) {
  const { timeout = 30000, signal: externalSignal, headers, ...rest } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  if (externalSignal) {
    externalSignal.addEventListener('abort', () => controller.abort(), { once: true })
  }
  // A FormData body must keep the browser-generated boundary, so only default
  // to JSON when the caller did not choose a content type.
  const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData
  let response
  try {
    response = await fetch(path, {
      headers: isFormData ? headers : { 'Content-Type': 'application/json', ...headers },
      ...rest,
      signal: controller.signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') {
      const timeoutError = new Error(`请求超时（${Math.round(timeout / 1000)} 秒无响应）`)
      timeoutError.code = 'timeout'
      throw timeoutError
    }
    throw err
  } finally {
    clearTimeout(timer)
  }

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    const message = payload?.error?.message || `请求失败 (${response.status})`
    const error = new Error(message)
    error.code = payload?.error?.code || 'http_error'
    error.status = response.status
    throw error
  }
  return payload
}

export function fetchHealth() {
  return request('/api/health', { timeout: 8000 })
}

/** Search resolves every returned entry, so it gets a generous budget. */
export function searchTracks(keyword, platform, limit = 8) {
  return request('/api/music/search', {
    method: 'POST',
    body: JSON.stringify({ keyword, platform, limit }),
    timeout: 45000,
  })
}

export function startDownload(track, format = 'wav') {
  // artist/duration travel with the request: sidecar sources (NetEase/KuGou)
  // have no metadata step, so the search leg's knowledge is all the cache
  // entry gets.
  return request('/api/music/download-audio', {
    method: 'POST',
    body: JSON.stringify({
      source: track.source,
      sourceId: track.sourceId,
      format,
      title: track.title,
      artist: track.artist,
      duration: track.duration,
    }),
    timeout: 15000,
  })
}

export function fetchTask(taskId) {
  return request(`/api/music/download-audio/${taskId}`, { timeout: 10000 })
}

/** Plain LRC text for a downloaded track; the player's lyrics panel renders it. */
export function fetchLyrics(source, sourceId) {
  return request(
    `/api/music/lyrics?source=${encodeURIComponent(source)}&sourceId=${encodeURIComponent(sourceId)}`,
    { timeout: 20000 },
  )
}

export function eventsUrl() {
  return '/api/events'
}

export function streamUrl(taskId) {
  return `/api/music/download-audio/stream?task_id=${encodeURIComponent(taskId)}`
}

export function fetchQueue() {
  return request('/api/music/download-audio/queue', { timeout: 8000 })
}

export function fetchCacheStatus() {
  return request('/api/cache/status', { timeout: 10000 })
}

/**
 * What the piano-transcription module may offer: faithful fidelity modes, the
 * transcribers, and the preview cap. Served by the backend so there is one
 * source of truth rather than a copy in the UI.
 */
export function fetchTranscribeOptions() {
  return request('/api/score/transcribe/options', { timeout: 10000 })
}

/** What the pop-to-piano module may offer: adding patterns and textures. */
export function fetchArrangeOptions() {
  return request('/api/score/arrange/options', { timeout: 10000 })
}

/**
 * Transcribe a cached piano recording into a faithful score.
 *
 * The analysis is CPU-bound for seconds (measured 91x realtime, so a full song
 * is a few seconds, but a long preview on a slow machine is longer), so the
 * budget here is deliberately much larger than every other call.
 *
 * That budget is sized for the default transcriber. `transkun` measured 20.9 s on
 * 20 s of audio where basic-pitch took 2.0 s - roughly a quarter of realtime - so
 * asking for it on a four-minute song is about a minute of transcription before
 * the score is written.
 */
export function transcribeScore(options) {
  const { cacheKey, fidelity, difficulty, fillChords, previewSeconds, title, transcriber } = options
  return request('/api/score/transcribe', {
    method: 'POST',
    body: JSON.stringify({
      cacheKey,
      fidelity,
      difficulty,
      fillChords,
      previewSeconds,
      title,
      transcriber,
    }),
    timeout: 900000,
  })
}

/**
 * Turn a cached recording into a playable piano arrangement: the right hand
 * keeps the transcribed melody, the left hand is rebuilt from patterns or
 * textures.
 */
export function arrangeScore(options) {
  const {
    cacheKey,
    fidelity,
    difficulty,
    pattern,
    previewSeconds,
    title,
    transcriber,
    texture,
    texturePlan,
    accompaniment,
    sectionTextures,
  } = options
  return request('/api/score/arrange', {
    method: 'POST',
    body: JSON.stringify({
      cacheKey,
      fidelity,
      difficulty,
      pattern,
      previewSeconds,
      title,
      transcriber,
      texture,
      texturePlan,
      accompaniment: accompaniment || undefined,
      sectionTextures: sectionTextures || undefined,
    }),
    timeout: 900000,
  })
}

/**
 * Upload a local piano recording and transcribe it in one round trip.
 *
 * fetch() cannot set a timeout, and the default is none, so the signal is
 * handled by the same AbortController path as every other request rather than
 * leaving the button stuck on "分析中" forever.
 */
export async function uploadAndTranscribe(file, options = {}) {
  const form = new FormData()
  form.append('analyse', options.analyse === false ? '0' : '1')
  form.append('fidelity', options.fidelity || 'transcribed')
  form.append('fillChords', options.fillChords ? 'true' : 'false')
  form.append('difficulty', `L${options.difficulty || 3}`)
  if (options.previewSeconds) form.append('previewSeconds', String(options.previewSeconds))
  // Sent on both paths. The backend reads this field from the multipart form as well as
  // from JSON; omitting it here would run basic-pitch no matter what the user chose.
  if (options.transcriber) form.append('transcriber', options.transcriber)
  const { signal, timeout = 900000, ...rest } = options
  return request('/api/score/transcribe/upload', { method: 'POST', body: form, timeout, signal, ...rest })
}

/** Upload a local recording and arrange it as piano in one round trip. */
export async function uploadAndArrange(file, options = {}) {
  const form = new FormData()
  form.append('analyse', options.analyse === false ? '0' : '1')
  form.append('fidelity', options.fidelity || 'arranged')
  form.append('difficulty', `L${options.difficulty || 3}`)
  if (options.pattern) form.append('pattern', options.pattern)
  if (options.previewSeconds) form.append('previewSeconds', String(options.previewSeconds))
  if (options.transcriber) form.append('transcriber', options.transcriber)
  if (options.texture) form.append('texture', options.texture)
  if (options.texturePlan) form.append('texturePlan', options.texturePlan)
  if (options.accompaniment) form.append('accompaniment', options.accompaniment)
  if (options.sectionTextures) form.append('sectionTextures', JSON.stringify(options.sectionTextures))
  const { signal, timeout = 900000, ...rest } = options
  return request('/api/score/arrange/upload', { method: 'POST', body: form, timeout, signal, ...rest })
}

/**
 * Detect the song's sections (intro/verse/chorus/interlude/bridge/outro) for a
 * cached track. Runs Whisper once per song (~30-60s), then served from cache.
 */
export function fetchArrangeSections(cacheKey) {
  return request(
    `/api/score/arrange/sections?cacheKey=${encodeURIComponent(cacheKey)}`,
    { timeout: 900000 },
  )
}

export function scoreMusicXmlUrl() {
  return '/api/score/musicxml'
}

export function scoreMidiUrl() {
  return '/api/score/midi'
}

/** Streams a cached audio file (Range aware) so the browser can play it. */
export function cacheAudioUrl(key) {
  return `/api/cache/audio/${encodeURIComponent(key)}`
}

/**
 * Bilibili's cover CDN blocks hotlinks from other origins (403), so covers are
 * proxied by the backend with a bilibili.com Referer.
 */
export function coverUrl(url) {
  return url ? `/api/media/cover?url=${encodeURIComponent(url)}` : ''
}

export function cleanCache(mode = 'cleanup') {
  return request('/api/cache/clean', {
    method: 'POST',
    body: JSON.stringify({ mode }),
    timeout: 20000,
  })
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}

export function formatDuration(seconds) {
  if (!seconds) return '--:--'
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export const TERMINAL_STAGES = new Set(['done', 'failed', 'cancelled'])
