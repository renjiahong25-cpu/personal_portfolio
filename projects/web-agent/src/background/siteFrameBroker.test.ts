import { describe, expect, it, vi } from 'vitest'
import { createSiteFrameBroker, FRAME_ASSIGN_MESSAGE } from './siteFrameBroker'

type FocusFn = (urlPrefix: string, startUrl: string) => Promise<{ tabId?: number; created: boolean }>
type SendFn = (tabId: number, message: Record<string, unknown>) => Promise<unknown>

function createHarness(overrides: { focusSiteTab?: FocusFn; sendToTab?: SendFn } = {}) {
  let clock = 1000
  const focusSiteTab = vi.fn(overrides.focusSiteTab ?? (async (): Promise<{ tabId?: number; created: boolean }> => ({ tabId: 42, created: true })))
  const sendToTab = vi.fn(overrides.sendToTab ?? (async (): Promise<unknown> => ({})))
  const log = vi.fn()
  const broker = createSiteFrameBroker({
    focusSiteTab,
    sendToTab,
    waitFor: async () => {
      clock += 500
    },
    now: () => clock,
    log,
  })
  return { broker, focusSiteTab, sendToTab, log }
}

describe('site frame broker', () => {
  it('focuses the site tab and assigns the frame role once the content script responds', async () => {
    const { broker, focusSiteTab, sendToTab, log } = createHarness()

    const result = await broker.ensureSiteFrame({
      chatId: 'chat-1',
      roleId: 'role-1',
      chatSite: 'deepseek',
      origin: 'https://chat.deepseek.com',
      startUrl: 'https://chat.deepseek.com/',
    })

    expect(result).toEqual({ chatId: 'chat-1', roleId: 'role-1', tabId: 42, assigned: true, attempts: 1 })
    expect(focusSiteTab).toHaveBeenCalledWith('https://chat.deepseek.com/', 'https://chat.deepseek.com/')
    expect(sendToTab).toHaveBeenCalledTimes(1)
    expect(sendToTab).toHaveBeenCalledWith(42, { type: FRAME_ASSIGN_MESSAGE, chatId: 'chat-1', roleId: 'role-1' })
    expect(log).toHaveBeenCalledWith('site-frame:tab-located', expect.objectContaining({ tabId: 42, created: true }))
    expect(log).toHaveBeenCalledWith('site-frame:assigned', expect.objectContaining({ attempt: 1 }))
  })

  it('retries assignment while the page is still loading until the content script is up', async () => {
    const { broker, sendToTab } = createHarness({
      sendToTab: async () => {
        if (sendToTab.mock.calls.length < 3) throw new Error('Could not establish connection. Receiving end does not exist.')
        return {}
      },
    })

    const result = await broker.ensureSiteFrame({
      chatId: 'chat-1',
      roleId: 'role-1',
      origin: 'https://www.doubao.com',
      startUrl: 'https://www.doubao.com/',
    })

    expect(result.assigned).toBe(true)
    expect(result.attempts).toBe(3)
    expect(sendToTab).toHaveBeenCalledTimes(3)
  })

  it('gives up after exhausting the retry budget and reports assigned=false without throwing', async () => {
    const { broker, sendToTab, log } = createHarness({
      sendToTab: async () => {
        throw new Error('Could not establish connection. Receiving end does not exist.')
      },
    })

    const result = await broker.ensureSiteFrame({
      chatId: 'chat-1',
      roleId: 'role-1',
      origin: 'https://chat.deepseek.com',
      startUrl: 'https://chat.deepseek.com/',
    })

    expect(result.assigned).toBe(false)
    expect(result.tabId).toBe(42)
    expect(sendToTab).toHaveBeenCalledTimes(60)
    expect(log).toHaveBeenCalledWith('site-frame:assign-failed', expect.objectContaining({ error: 'max attempts' }))
  })

  it('throws when no tab can be located', async () => {
    const { broker } = createHarness({ focusSiteTab: async () => ({ created: false }) })

    await expect(
      broker.ensureSiteFrame({ chatId: 'chat-1', roleId: 'role-1', origin: 'https://chat.deepseek.com', startUrl: 'https://chat.deepseek.com/' }),
    ).rejects.toThrow('无法定位站点标签页')
  })
})
