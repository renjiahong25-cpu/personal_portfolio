import { describe, expect, it, vi } from 'vitest'
import type { RuntimeMessage } from './runtimeClient'

describe('background message router', () => {
  it('dispatches runtime messages by type and passes sender context', async () => {
    const { createMessageRouter } = await import('./messageRouter')
    const sender = { tab: { id: 101 } as chrome.tabs.Tab, frameId: 7 }
    const handler = vi.fn(async (message: RuntimeMessage, currentSender: chrome.runtime.MessageSender) => ({
      ok: true,
      type: message.type,
      tabId: currentSender.tab?.id,
    }))
    const router = createMessageRouter([
      { type: 'GROUP_STORE_GET', handler },
    ])

    await expect(router({ type: 'GROUP_STORE_GET', hostTabId: 101 }, sender)).resolves.toEqual({
      ok: true,
      type: 'GROUP_STORE_GET',
      tabId: 101,
    })
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('uses the fallback for unknown message types', async () => {
    const { createMessageRouter } = await import('./messageRouter')
    const fallback = vi.fn(async message => ({ ok: false, error: `Unknown: ${message.type}` }))
    const router = createMessageRouter([], fallback)

    await expect(router({ type: 'UNKNOWN' }, {})).resolves.toEqual({ ok: false, error: 'Unknown: UNKNOWN' })
    expect(fallback).toHaveBeenCalledTimes(1)
  })
})
