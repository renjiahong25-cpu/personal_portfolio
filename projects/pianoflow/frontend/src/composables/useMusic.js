/**
 * Shared play/download behaviour for the cover walls:
 *  - local entries play straight from the cache
 *  - search hits play as soon as an mp3 exists locally, otherwise the mp3 is
 *    downloaded first and playback starts when it lands
 *  - "download wav" feeds the transcription pipeline
 */
import { watch } from 'vue'
import { coverUrl } from '../api'
import { usePlayer } from './usePlayer'
import { useDownloads } from './useDownloads'
import { useLibrary } from './useLibrary'

export function useMusic() {
  const player = usePlayer()
  const downloads = useDownloads()
  const library = useLibrary()

  function playLibrary(entry) {
    player.loadTrack(
      {
        id: `cache:${entry.key}`,
        title: entry.title,
        artist: entry.artist,
        cover: coverUrl(entry.thumbnail),
        url: entry.audioUrl,
        lyrics: { source: entry.source, sourceId: entry.sourceId },
        origin: { source: entry.source, sourceId: entry.sourceId },
      },
      { autoplay: true },
    )
  }

  function awaitTask(uid) {
    return new Promise((resolve, reject) => {
      const stop = watch(
        () => downloads.tasks[uid],
        (task) => {
          if (!task) return
          if (task.stage === 'done') {
            stop()
            resolve(task)
          } else if (task.stage === 'failed' || task.stage === 'cancelled') {
            stop()
            reject(new Error(task.error?.message || task.message || '任务失败'))
          }
        },
        { immediate: true },
      )
    })
  }

  async function playSearch(track) {
    const existing = library.state.entries.find(
      (e) => e.source === track.source && e.sourceId === track.sourceId && e.format === 'mp3',
    )
    if (existing) {
      playLibrary(existing)
      return
    }
    // Keep the track's own uid: the card looks up progress/errors by it.
    const uid = track.uid || `srch:${track.source}:${track.sourceId}`
    try {
      await downloads.download({ ...track, uid }, 'mp3')
      const task = await awaitTask(uid)
      await library.refresh()
      const entry = library.state.entries.find(
        (e) => e.source === track.source && e.sourceId === track.sourceId,
      )
      if (!entry) {
        const url = task.result?.audioUrl
        if (url) {
          player.loadTrack(
            {
              id: `srch:${track.source}:${track.sourceId}`,
              title: track.title,
              artist: track.artist,
              cover: coverUrl(track.thumbnail),
              url,
              lyrics: { source: track.source, sourceId: track.sourceId },
              origin: { source: track.source, sourceId: track.sourceId },
            },
            { autoplay: true },
          )
        }
        return
      }
      playLibrary(entry)
    } catch (err) {
      downloads.errors[uid] = err.message
      throw err
    }
  }

  async function downloadWav(track) {
    const uid = track.uid || `wav:${track.source}:${track.sourceId}`
    await downloads.download({ ...track, uid }, 'wav')
    return awaitTask(uid)
  }

  function isPlayingLibrary(entry) {
    return player.isTrackLoaded({ id: `cache:${entry.key}` })
  }

  /** Which card owns the current track (works for both local and search ids). */
  function isPlayingTrack(track) {
    const p = player.state.track
    return Boolean(
      p?.origin &&
        track?.source &&
        p.origin.source === track.source &&
        String(p.origin.sourceId) === String(track.sourceId),
    )
  }

  return { player, downloads, library, playLibrary, playSearch, downloadWav, isPlayingLibrary, isPlayingTrack }
}
