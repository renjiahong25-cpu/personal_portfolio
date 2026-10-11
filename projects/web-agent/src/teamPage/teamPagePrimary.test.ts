import { describe, expect, it, vi } from 'vitest'
import { createTeamPagePrimaryCoordinator, TEAM_PAGE_PRIMARY_LOCK_NAME } from './teamPagePrimary'

describe('team page primary coordinator', () => {
  it('becomes primary when the browser grants the page lock', async () => {
    const onPrimaryChange = vi.fn()
    const lockRequest = vi.fn((_name, _options, callback) => {
      const result = callback({})
      if (result instanceof Promise) result.then(() => undefined)
      return Promise.resolve()
    })
    const coordinator = createTeamPagePrimaryCoordinator({
      navigator: { locks: { request: lockRequest } } as never,
      onPrimaryChange,
    })

    const isPrimary = await coordinator.start()

    expect(isPrimary).toBe(true)
    expect(coordinator.isPrimary()).toBe(true)
    expect(lockRequest).toHaveBeenCalledWith(TEAM_PAGE_PRIMARY_LOCK_NAME, { ifAvailable: true }, expect.any(Function))
    expect(onPrimaryChange).toHaveBeenCalledWith(true)
    coordinator.dispose()
  })

  it('stays passive when another team page already holds the lock', async () => {
    const onPrimaryChange = vi.fn()
    const lockRequest = vi.fn((_name, _options, callback) => Promise.resolve(callback(null)))
    const coordinator = createTeamPagePrimaryCoordinator({
      navigator: { locks: { request: lockRequest } } as never,
      window: { setTimeout: vi.fn(() => 1), clearTimeout: vi.fn() } as never,
      onPrimaryChange,
    })

    const isPrimary = await coordinator.start()

    expect(isPrimary).toBe(false)
    expect(coordinator.isPrimary()).toBe(false)
    expect(onPrimaryChange).toHaveBeenCalledWith(false)
  })

  it('falls back to primary mode when Web Locks are unavailable', async () => {
    const onPrimaryChange = vi.fn()
    const coordinator = createTeamPagePrimaryCoordinator({
      navigator: {} as never,
      onPrimaryChange,
    })

    const isPrimary = await coordinator.start()

    expect(isPrimary).toBe(true)
    expect(coordinator.isPrimary()).toBe(true)
    expect(onPrimaryChange).toHaveBeenCalledWith(true)
  })
})
