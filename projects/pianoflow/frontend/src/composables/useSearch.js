/**
 * Search state that survives tab switches.
 *
 * App.vue renders the two views with ``v-if``, so a view is destroyed when you
 * switch tabs. Keeping the results in module scope means coming back to
 * "搜索与下载" shows the same cards instead of an empty page.
 */

import { reactive } from 'vue'
import { coverUrl, formatDuration, searchTracks } from '../api'

const state = reactive({
  keyword: '',
  platform: 'bilibili',
  limit: 8,
  results: [],
  selectedUid: '',
  error: '',
  notice: '',
  searching: false,
  lastQuery: '',
  brokenCovers: {},
})

export function useSearch() {
  function selected() {
    return state.results.find((track) => track.uid === state.selectedUid) || null
  }

  function select(track) {
    state.selectedUid = track.uid
  }

  function markCoverBroken(uid) {
    state.brokenCovers = { ...state.brokenCovers, [uid]: true }
  }

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
      const payload = await searchTracks(query, state.platform, state.limit)
      state.results = payload.list
      state.lastQuery = query
      state.selectedUid = payload.list[0]?.uid || ''
      if (!payload.list.length) {
        state.error = `没有搜到「${query}」的结果。换个说法再试，例如「星辰大海 邓丽君」。`
      } else if (payload.elapsedSeconds > 8) {
        state.notice = `解析了 ${payload.count} 条，用时 ${payload.elapsedSeconds} 秒。`
      }
    } catch (err) {
      state.error =
        err.code === 'upstream_blocked' ? err.message : `搜索失败：${err.message}`
      state.results = []
      state.selectedUid = ''
    } finally {
      state.searching = false
    }
  }

  function clear() {
    state.results = []
    state.selectedUid = ''
    state.error = ''
    state.notice = ''
  }

  return {
    state,
    selected,
    select,
    search,
    clear,
    markCoverBroken,
    coverUrl,
    formatDuration,
  }
}
