/**
 * The local library: everything in the audio cache, module scope so the home
 * wall and every platform sidebar share one list.
 */
import { reactive } from 'vue'
import { cacheAudioUrl, fetchCacheStatus } from '../api'

const state = reactive({
  entries: [],
  loading: false,
  error: '',
  totalBytes: 0,
})

async function refresh() {
  state.loading = true
  state.error = ''
  try {
    const payload = await fetchCacheStatus()
    state.entries = (payload.entries || []).map((e) => ({
      uid: `lib:${e.key}`,
      key: e.key,
      source: e.source,
      sourceId: e.sourceId,
      title: e.title,
      artist: e.artist || '',
      duration: e.duration,
      format: e.format,
      sizeBytes: e.sizeBytes,
      webpageUrl: e.webpageUrl || '',
      thumbnail: e.thumbnail || '',
      audioUrl: cacheAudioUrl(e.key),
      createdAt: e.createdAt,
    }))
    state.totalBytes = payload.totalBytes || 0
  } catch (err) {
    state.error = err.message
  } finally {
    state.loading = false
  }
}

export function useLibrary() {
  return { state, refresh }
}
