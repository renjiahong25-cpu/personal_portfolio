import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'

describe('team page runtime client', () => {
  it('adds hostTabId to outgoing messages and applies command stores', async () => {
    vi.resetModules()
    const runtimeSendMessage = vi.fn((_message, callback) => callback({ ok: true, store: createDefaultStore() }))
    vi.stubGlobal('chrome', {
      runtime: {
        lastError: undefined,
        sendMessage: runtimeSendMessage,
      },
    })

    const { createTeamPageRuntimeClient } = await import('./runtimeClient')
    const applyStore = vi.fn()
    const refreshStore = vi.fn()
    const client = createTeamPageRuntimeClient({
      getHostTabId: () => 321,
      applyStore,
      refreshStore,
      log: { debug: vi.fn(), warn: vi.fn() },
    })

    await client.runCommand('GROUP_CHAT_SWITCH', { chatId: 'chat-1' })

    expect(runtimeSendMessage).toHaveBeenCalledWith(
      { type: 'GROUP_CHAT_SWITCH', chatId: 'chat-1', hostTabId: 321 },
      expect.any(Function),
    )
    expect(applyStore).toHaveBeenCalledWith(expect.objectContaining({ version: expect.any(Number) }))
    expect(refreshStore).not.toHaveBeenCalled()
  })
})
