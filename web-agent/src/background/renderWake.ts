export const RENDER_WAKE_INTERVAL_MS = 6000
export const RENDER_WAKE_VISIBLE_MS = 1800

interface RenderWakeTab {
  id?: number
}

interface RenderWakeTabsApi {
  update(tabId: number, updateProperties: { active: boolean }): Promise<unknown>
  query(queryInfo: { active: boolean; currentWindow: boolean }): Promise<RenderWakeTab[]>
}

interface ScheduledWake {
  cancelled: boolean
  timer?: ReturnType<typeof setTimeout>
}

function wait(ms: number): Promise<void> {
  return new Promise(resolve => {
    setTimeout(resolve, ms)
  })
}

export function createRenderWakeScheduler(tabs: RenderWakeTabsApi) {
  const scheduledByTab = new Map<number, ScheduledWake>()
  let wakeQueue = Promise.resolve()

  async function wakeOnce(tabId: number, hostTabId: number, scheduled: ScheduledWake): Promise<void> {
    if (scheduled.cancelled) return
    const [activeTab] = await tabs.query({ active: true, currentWindow: true })
    const restoreTabId = activeTab?.id ?? (hostTabId >= 0 ? hostTabId : undefined)

    await tabs.update(tabId, { active: true })
    await wait(RENDER_WAKE_VISIBLE_MS)
    if (restoreTabId !== undefined && restoreTabId !== tabId) {
      await tabs.update(restoreTabId, { active: true })
    }
  }

  function enqueueWake(tabId: number, hostTabId: number, scheduled: ScheduledWake): Promise<void> {
    const nextWake = wakeQueue.then(() => wakeOnce(tabId, hostTabId, scheduled)).catch(() => undefined)
    wakeQueue = nextWake
    return nextWake
  }

  function scheduleNext(tabId: number, hostTabId: number, scheduled: ScheduledWake): void {
    if (scheduled.cancelled) return

    scheduled.timer = setTimeout(() => {
      scheduled.timer = undefined
      enqueueWake(tabId, hostTabId, scheduled).then(() => {
        scheduleNext(tabId, hostTabId, scheduled)
      })
    }, RENDER_WAKE_INTERVAL_MS)
  }

  return {
    schedule(tabId: number, hostTabId: number): void {
      this.cancel(tabId)

      const scheduled: ScheduledWake = { cancelled: false }
      scheduledByTab.set(tabId, scheduled)
      scheduleNext(tabId, hostTabId, scheduled)
    },

    cancel(tabId: number): void {
      const scheduled = scheduledByTab.get(tabId)
      if (!scheduled) return

      scheduled.cancelled = true
      if (scheduled.timer) clearTimeout(scheduled.timer)
      scheduledByTab.delete(tabId)
    },
  }
}
