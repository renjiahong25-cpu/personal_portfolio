import type { SiteStatus } from '../group/runtimeProtocol'

export interface SiteStatusRecord {
  status: SiteStatus
  detail?: string
  timestamp: number
}

export interface SiteStatusRegistry {
  get(siteId: string): SiteStatusRecord | undefined
  set(siteId: string, status: SiteStatus, detail: string | undefined, timestamp: number): void
  onUnauthorizedToReady(listener: (siteId: string) => void): () => void
}

export function createSiteStatusRegistry(): SiteStatusRegistry {
  const records = new Map<string, SiteStatusRecord>()
  const listeners = new Set<(siteId: string) => void>()

  return {
    get(siteId) {
      return records.get(siteId)
    },
    set(siteId, status, detail, timestamp) {
      const previous = records.get(siteId)
      records.set(siteId, { status, detail, timestamp })
      if (previous?.status === 'unauthorized' && status === 'ready') {
        for (const listener of [...listeners]) listener(siteId)
      }
    },
    onUnauthorizedToReady(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
}