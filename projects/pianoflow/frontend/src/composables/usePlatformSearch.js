/**
 * Per-platform search state, module scope: switching tabs destroys the views,
 * but "what did I search on NetEase" must survive the trip.
 */
import { reactive } from 'vue'
import { coverUrl, formatDuration, searchTracks } from '../api'

const store = new Map()

export function usePlatformSearch(platform) {
  if (!store.has(platform)) {
    store.set(platform, reactive({
      keyword: '',
      limit: 8,
      results: [],
      searched: false,
      searching: false,
      error: '',
      notice: '',
      lastQuery: '',
      brokenCovers: {},
    }))
  }
  const state = store.get(platform)

  async function search() {
    const query = state.keyword.trim()
    if (!query) {
      state.error = '请先输入歌名'
      return
    }
    state.searching = true
    state.error = ''
    state.notice = ''
    state.brokenCovers = {}
    try {
      const payload = await searchTracks(query, platform, state.limit)
      state.results = payload.list
      state.searched = true
      state.lastQuery = query
      if (!payload.list.length) {
        state.error = `没有搜到「${query}」的结果。换个说法再试。`
      } else if (payload.elapsedSeconds > 8) {
        state.notice = `解析了 ${payload.count} 条，用时 ${payload.elapsedSeconds} 秒。`
      }
    } catch (err) {
      state.error = err.code === 'upstream_blocked' ? err.message : `搜索失败：${err.message}`
      state.results = []
    } finally {
      state.searching = false
    }
  }

  function markCoverBroken(uid) {
    state.brokenCovers = { ...state.brokenCovers, [uid]: true }
  }

  return { state, search, markCoverBroken, coverUrl, formatDuration }
}
