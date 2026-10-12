/**
 * Single source of truth for download state.
 *
 * Design constraints that shaped this file:
 *
 * 1. Module scope, so switching tabs never loses progress.
 * 2. **One** EventStream for the whole page (`/api/events`), not one per
 *    download. Browsers cap concurrent connections per origin (6 under
 *    HTTP/1.1); the previous per-task design locked out every other request and
 *    made the page look frozen.
 * 3. Polling fallback: if the stream dies we degrade to 2s polling of the task
 *    snapshots instead of showing a dead progress bar.
 */

import { computed, reactive } from 'vue'
import { eventsUrl, fetchTask, startDownload, TERMINAL_STAGES } from '../api'

const POLL_INTERVAL_MS = 2000

const tasks = reactive({})
const errors = reactive({})
const taskToUid = new Map()

let session = null
let poller = null
let reconnectTimer = null

function uidFor(taskId) {
  return taskToUid.get(taskId)
}

function merge(uid, snapshot) {
  if (!uid) return
  tasks[uid] = { ...(tasks[uid] || {}), ...snapshot, uid }
}

function activeEntries() {
  return Object.entries(tasks).filter(([, task]) => !TERMINAL_STAGES.has(task.stage))
}

function stopPolling() {
  if (poller) {
    clearInterval(poller)
    poller = null
  }
}

function startPolling() {
  if (poller) return
  poller = setInterval(async () => {
    const entries = activeEntries()
    if (!entries.length) {
      stopPolling()
      return
    }
    for (const [uid, task] of entries) {
      try {
        merge(uid, await fetchTask(task.taskId))
      } catch {
        // keep the last known state; the next tick tries again
      }
    }
  }, POLL_INTERVAL_MS)
}

function handleSnapshot(snapshot) {
  const uid = uidFor(snapshot.taskId)
  if (!uid) return
  merge(uid, snapshot)
  if (!activeEntries().length) stopPolling()
}

function openSession() {
  if (session) return
  session = new EventSource(eventsUrl())
  const stages = [
    'queued',
    'resolving',
    'downloading',
    'transcoding',
    'done',
    'failed',
    'cancelled',
  ]
  for (const stage of stages) {
    session.addEventListener(stage, (event) => {
      try {
        handleSnapshot(JSON.parse(event.data))
      } catch {
        /* ignore malformed frame */
      }
    })
  }
  session.onerror = () => {
    // EventSource retries by itself; meanwhile polling keeps the UI truthful.
    closeSession()
    startPolling()
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        openSession()
      }, 3000)
    }
  }
}

function closeSession() {
  if (session) {
    session.close()
    session = null
  }
}

export function useDownloads() {
  async function download(track, format = 'wav') {
    const uid = track.uid
    delete errors[uid]
    try {
      const task = await startDownload(track, format)
      taskToUid.set(task.taskId, uid)
      merge(uid, task)
      openSession()
      // Polling always runs while something is in flight: it is cheap, and it
      // is the only thing keeping the UI correct if the stream never arrives.
      startPolling()
      return task
    } catch (err) {
      errors[uid] = err.message
      throw err
    }
  }

  function stageOf(uid) {
    return tasks[uid]?.stage || ''
  }

  function isBusy(uid) {
    const stage = stageOf(uid)
    return Boolean(stage) && !TERMINAL_STAGES.has(stage)
  }

  function isDone(uid) {
    return stageOf(uid) === 'done'
  }

  const busyUids = computed(() => Object.keys(tasks).filter(isBusy))
  const finished = computed(() =>
    Object.values(tasks)
      .filter((task) => TERMINAL_STAGES.has(task.stage))
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)),
  )

  return { tasks, errors, download, stageOf, isBusy, isDone, busyUids, finished }
}
