import type { ChatSite } from '../group/types'

export interface SiteFrameAssignment {
  chatId: string
  roleId: string
  chatSite?: ChatSite
  origin: string
  startUrl: string
}

export interface SiteFrameBrokerDependencies {
  focusSiteTab(urlPrefix: string, startUrl: string): Promise<{ tabId?: number; created: boolean }>
  sendToTab(tabId: number, message: Record<string, unknown>): Promise<unknown>
  waitFor(ms: number): Promise<void>
  now(): number
  log(event: string, details?: Record<string, unknown>): void
}

export interface SiteFrameBrokerResult {
  chatId: string
  roleId: string
  tabId?: number
  assigned: boolean
  attempts: number
}

export const FRAME_ASSIGN_MESSAGE = 'WEB_AGENT_ASSIGN_FRAME_ROLE'
const RETRY_INTERVAL_MS = 500
const MAX_ATTEMPTS = 60

export function createSiteFrameBroker(deps: SiteFrameBrokerDependencies) {
  async function ensureSiteFrame(input: SiteFrameAssignment): Promise<SiteFrameBrokerResult> {
    const { tabId, created } = await deps.focusSiteTab(`${input.origin}/`, input.startUrl)
    if (!tabId) {
      throw new Error('无法定位站点标签页')
    }
    deps.log('site-frame:tab-located', { chatId: input.chatId, roleId: input.roleId, tabId, created, site: input.chatSite })

    const message = {
      type: FRAME_ASSIGN_MESSAGE,
      chatId: input.chatId,
      roleId: input.roleId,
    }
    const start = deps.now()
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
      try {
        await deps.sendToTab(tabId, message)
        deps.log('site-frame:assigned', { chatId: input.chatId, roleId: input.roleId, tabId, attempt })
        return { chatId: input.chatId, roleId: input.roleId, tabId, assigned: true, attempts: attempt }
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error)
        if (deps.now() - start >= 30_000) {
          deps.log('site-frame:assign-failed', { chatId: input.chatId, roleId: input.roleId, tabId, attempt, error: reason })
          return { chatId: input.chatId, roleId: input.roleId, tabId, assigned: false, attempts: attempt }
        }
        await deps.waitFor(RETRY_INTERVAL_MS)
      }
    }
    deps.log('site-frame:assign-failed', { chatId: input.chatId, roleId: input.roleId, tabId, error: 'max attempts' })
    return { chatId: input.chatId, roleId: input.roleId, tabId, assigned: false, attempts: MAX_ATTEMPTS }
  }

  return { ensureSiteFrame }
}
