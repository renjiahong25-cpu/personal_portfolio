export const TEAM_PAGE_PRIMARY_LOCK_NAME = 'web-agent:team-page:primary'

type LockLike = Record<string, never>
type LockRequestCallback = (lock: LockLike | null) => Promise<void> | void
type LockManagerLike = {
  request(name: string, options: { ifAvailable: true }, callback: LockRequestCallback): Promise<unknown>
}

export interface TeamPagePrimaryCoordinatorDependencies {
  navigator?: Navigator
  window?: Window
  retryDelayMs?: number
  onPrimaryChange(isPrimary: boolean): void
  log?: {
    warn(event: string, details?: Record<string, unknown>): void
  }
}

export interface TeamPagePrimaryCoordinator {
  start(): Promise<boolean>
  isPrimary(): boolean
  dispose(): void
}

const DEFAULT_RETRY_DELAY_MS = 2000

export function createTeamPagePrimaryCoordinator(deps: TeamPagePrimaryCoordinatorDependencies): TeamPagePrimaryCoordinator {
  const lockManager = (deps.navigator as (Navigator & { locks?: LockManagerLike }) | undefined)?.locks
  const retryDelayMs = deps.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS
  const timerWindow = deps.window ?? (typeof window === 'undefined' ? undefined : window)
  let primary = false
  let primaryStateKnown = false
  let disposed = false
  let releaseLock: (() => void) | undefined
  let retryTimer: number | undefined

  function setPrimary(nextPrimary: boolean): void {
    if (primaryStateKnown && primary === nextPrimary) return
    primaryStateKnown = true
    primary = nextPrimary
    deps.onPrimaryChange(primary)
  }

  function clearRetry(): void {
    if (retryTimer === undefined) return
    timerWindow?.clearTimeout(retryTimer)
    retryTimer = undefined
  }

  function scheduleRetry(): void {
    if (disposed || primary || retryTimer !== undefined || !lockManager?.request || !timerWindow) return
    retryTimer = timerWindow.setTimeout(() => {
      retryTimer = undefined
      void tryAcquire()
    }, retryDelayMs)
  }

  async function tryAcquire(): Promise<boolean> {
    if (disposed) return primary
    if (!lockManager?.request) {
      setPrimary(true)
      return true
    }

    return new Promise<boolean>(resolve => {
      lockManager.request(TEAM_PAGE_PRIMARY_LOCK_NAME, { ifAvailable: true }, lock => {
        if (!lock) {
          setPrimary(false)
          scheduleRetry()
          resolve(false)
          return
        }

        clearRetry()
        setPrimary(true)
        resolve(true)
        return new Promise<void>(release => {
          releaseLock = release
        })
      }).catch(error => {
        deps.log?.warn('team-page-primary:lock-failed', { error: error instanceof Error ? error.message : String(error) })
        setPrimary(true)
        resolve(true)
      })
    })
  }

  return {
    start: tryAcquire,
    isPrimary: () => primary,
    dispose: () => {
      disposed = true
      clearRetry()
      releaseLock?.()
      releaseLock = undefined
      setPrimary(false)
    },
  }
}
