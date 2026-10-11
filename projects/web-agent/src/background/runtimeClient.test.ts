import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { WebAgentControlConnectionStatus } from '../shared/localControlProtocol'

describe('background runtime client', () => {
  it('tracks host tabs and resolves sender/message tab identity', async () => {
    vi.resetModules()
    const { forgetHostTab, listHostTabIds, messageTabId, rememberHost, senderFrameId, senderTabId } = await import('./runtimeClient')

    rememberHost({ tab: { id: 101 } as chrome.tabs.Tab }, undefined)
    rememberHost({}, 202)

    expect(senderTabId({ tab: { id: 101 } as chrome.tabs.Tab })).toBe(101)
    expect(senderFrameId({ frameId: 7 })).toBe(7)
    expect(senderFrameId({})).toBe(0)
    expect(messageTabId({ hostTabId: 303 }, {})).toBe(303)
    expect(messageTabId({ hostTabId: 303 }, { tab: { id: 101 } as chrome.tabs.Tab })).toBe(101)
    expect(listHostTabIds()).toEqual([101, 202])

    forgetHostTab(101)
    expect(listHostTabIds()).toEqual([202])
  })

  it('broadcasts store updates to known hosts, prunes failed hosts, and sends runtime push payloads', async () => {
    vi.resetModules()
    const tabsSendMessage = vi.fn()
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error('tab closed'))
    const runtimeSendMessage = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('chrome', {
      tabs: { sendMessage: tabsSendMessage },
      runtime: { sendMessage: runtimeSendMessage },
    })
    const { broadcastStoreUpdated, listHostTabIds, rememberHost } = await import('./runtimeClient')
    const store = createDefaultStore()

    rememberHost({}, 101)
    rememberHost({}, 202)
    await broadcastStoreUpdated(store)

    expect(tabsSendMessage).toHaveBeenCalledTimes(2)
    expect(tabsSendMessage).toHaveBeenNthCalledWith(1, 101, { type: 'GROUP_STORE_UPDATED', store })
    expect(tabsSendMessage).toHaveBeenNthCalledWith(2, 202, { type: 'GROUP_STORE_UPDATED', store })
    expect(runtimeSendMessage).toHaveBeenCalledWith({ type: 'WEB_AGENT_GROUP_PUSH', payload: { type: 'GROUP_STORE_UPDATED', store } })
    expect(listHostTabIds()).toEqual([101])
  })

  it('broadcasts local control status updates to known hosts and runtime listeners', async () => {
    vi.resetModules()
    const tabsSendMessage = vi.fn().mockResolvedValue({ ok: true })
    const runtimeSendMessage = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('chrome', {
      tabs: { sendMessage: tabsSendMessage },
      runtime: { sendMessage: runtimeSendMessage },
    })
    const { broadcastControlStatusUpdated, rememberHost } = await import('./runtimeClient')
    const controlStatus: WebAgentControlConnectionStatus = { state: 'disconnected', port: 19305, lastError: 'Web Agent CLI daemon is not reachable.' }

    rememberHost({}, 101)
    await broadcastControlStatusUpdated(controlStatus)

    expect(tabsSendMessage).toHaveBeenCalledWith(101, { type: 'GROUP_CONTROL_STATUS_UPDATED', controlStatus })
    expect(runtimeSendMessage).toHaveBeenCalledWith({ type: 'WEB_AGENT_GROUP_PUSH', payload: { type: 'GROUP_CONTROL_STATUS_UPDATED', controlStatus } })
  })
})
