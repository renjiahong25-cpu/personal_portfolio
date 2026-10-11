import type { ChatSite } from '../group/types'

const DEFAULT_MAX_ACTIVE_BY_SITE: Partial<Record<ChatSite, number>> = {
  deepseek: 2,
}

export interface LimitedPromptDelivery {
  chatId: string
  messageId: string
  roleId: string
  chatSite?: ChatSite
  send(): Promise<boolean>
}

export interface SitePromptDeliveryLimiter {
  enqueue(delivery: LimitedPromptDelivery): Promise<void>
  complete(chatId: string, messageId: string, roleId: string): Promise<void>
}

export interface SitePromptDeliveryLimiterDependencies {
  log: {
    info(event: string, details?: Record<string, unknown>): void
    warn(event: string, details?: Record<string, unknown>): void
  }
  maxActiveBySite?: Partial<Record<ChatSite, number>>
}

interface SitePromptDeliveryLimitState {
  site: ChatSite
  pending: LimitedPromptDelivery[]
  activeKeys: Set<string>
}

export function createSitePromptDeliveryLimiter(deps: SitePromptDeliveryLimiterDependencies): SitePromptDeliveryLimiter {
  const maxActiveBySite = { ...DEFAULT_MAX_ACTIVE_BY_SITE, ...deps.maxActiveBySite }
  const states = new Map<ChatSite, SitePromptDeliveryLimitState>()
  const activeSiteByDeliveryKey = new Map<string, ChatSite>()

  const pump = async (state: SitePromptDeliveryLimitState): Promise<void> => {
    const maxActive = maxActiveBySite[state.site]
    if (!maxActive || maxActive < 1) return

    const launched: Array<Promise<void>> = []
    while (state.activeKeys.size < maxActive && state.pending.length > 0) {
      const delivery = state.pending.shift()!
      const key = deliveryKey(delivery.chatId, delivery.messageId, delivery.roleId)
      state.activeKeys.add(key)
      activeSiteByDeliveryKey.set(key, state.site)
      deps.log.info('site-prompt-limit:send', {
        site: state.site,
        chatId: delivery.chatId,
        messageId: delivery.messageId,
        roleId: delivery.roleId,
        activeCount: state.activeKeys.size,
        remainingCount: state.pending.length,
      })
      launched.push(delivery.send().then(async sent => {
        if (!sent) await complete(delivery.chatId, delivery.messageId, delivery.roleId)
      }).catch(async error => {
        deps.log.warn('site-prompt-limit:send-failed', {
          site: state.site,
          chatId: delivery.chatId,
          messageId: delivery.messageId,
          roleId: delivery.roleId,
          error: error instanceof Error ? error.message : String(error),
        })
        await complete(delivery.chatId, delivery.messageId, delivery.roleId)
        throw error
      }))
    }
    await Promise.all(launched)
  }

  const complete = async (chatId: string, messageId: string, roleId: string): Promise<void> => {
    const key = deliveryKey(chatId, messageId, roleId)
    const site = activeSiteByDeliveryKey.get(key)
    if (!site) return
    const state = states.get(site)
    activeSiteByDeliveryKey.delete(key)
    state?.activeKeys.delete(key)
    if (!state) return
    if (state.pending.length === 0 && state.activeKeys.size === 0) {
      states.delete(site)
      return
    }
    await pump(state)
  }

  return {
    async enqueue(delivery) {
      const site = delivery.chatSite
      const maxActive = site ? maxActiveBySite[site] : undefined
      if (!site || !maxActive || maxActive < 1) {
        await delivery.send()
        return
      }
      const state = states.get(site) ?? { site, pending: [], activeKeys: new Set<string>() }
      states.set(site, state)
      state.pending.push(delivery)
      await pump(state)
    },
    complete,
  }
}

function deliveryKey(chatId: string, messageId: string, roleId: string): string {
  return `${chatId}:${messageId}:${roleId}`
}
