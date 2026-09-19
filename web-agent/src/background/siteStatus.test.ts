import { describe, expect, it, vi } from 'vitest'
import { createSiteStatusRegistry } from './siteStatus'

describe('site status registry', () => {
  it('stores the latest status per site', () => {
    const registry = createSiteStatusRegistry()
    expect(registry.get('deepseek')).toBeUndefined()

    registry.set('deepseek', 'unauthorized', '请先登录', 1)
    expect(registry.get('deepseek')).toEqual({ status: 'unauthorized', detail: '请先登录', timestamp: 1 })

    registry.set('deepseek', 'ready', undefined, 2)
    expect(registry.get('deepseek')).toEqual({ status: 'ready', timestamp: 2 })
  })

  it('fires the unauthorized-to-ready listener exactly once', () => {
    const registry = createSiteStatusRegistry()
    const listener = vi.fn()
    registry.onUnauthorizedToReady(listener)

    registry.set('deepseek', 'unauthorized', undefined, 1)
    registry.set('deepseek', 'unauthorized', undefined, 2)
    expect(listener).not.toHaveBeenCalled()

    registry.set('deepseek', 'ready', undefined, 3)
    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith('deepseek')

    registry.set('deepseek', 'ready', undefined, 4)
    registry.set('deepseek', 'generating', undefined, 5)
    registry.set('deepseek', 'unauthorized', undefined, 6)
    registry.set('deepseek', 'ready', undefined, 7)
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it('stops calling the listener once unsubscribed', () => {
    const registry = createSiteStatusRegistry()
    const listener = vi.fn()
    const unsubscribe = registry.onUnauthorizedToReady(listener)
    unsubscribe()

    registry.set('deepseek', 'unauthorized', undefined, 1)
    registry.set('deepseek', 'ready', undefined, 2)
    expect(listener).not.toHaveBeenCalled()
  })
})