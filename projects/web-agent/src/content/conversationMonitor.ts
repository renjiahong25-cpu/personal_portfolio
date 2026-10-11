import type { RoleToBackgroundMessage } from '../group/runtimeProtocol'
import type { ContentLogger } from './runtimeClient'
import type { RoleSession } from './roleSession'
import type { ChatSiteAdapter } from './sites/types'

export interface ConversationMonitor {
  reportConversationUpdate(force?: boolean): void
  start(): void
}

export function createConversationMonitor(options: {
  siteAdapter: ChatSiteAdapter
  roleSession: RoleSession
  log: ContentLogger
  sendRuntimeMessage<T>(message: RoleToBackgroundMessage): Promise<T>
}): ConversationMonitor {
  let lastReportedConversationKey = ''
  let conversationMonitorStarted = false

  function reportConversationUpdate(force = false): void {
    const assignedRole = options.roleSession.getAssignedRole()
    if (!assignedRole) return

    const chatId = options.roleSession.getAssignedChatId(assignedRole)
    if (!chatId) return

    const snapshot = options.siteAdapter.getConversationSnapshot()
    const key = `${chatId}:${assignedRole.roleId}:${snapshot.conversationId || ''}:${snapshot.conversationUrl || ''}`
    if (!force && key === lastReportedConversationKey) return
    lastReportedConversationKey = key

    options
      .sendRuntimeMessage({
        type: 'TEAM_ROLE_CONVERSATION_UPDATED',
        chatId,
        roleId: assignedRole.roleId,
        conversationId: snapshot.conversationId,
        conversationUrl: snapshot.conversationUrl,
      })
      .catch(error => options.log.warn('conversation-update:failed', { error: error instanceof Error ? error.message : String(error) }))
  }

  function start(): void {
    if (conversationMonitorStarted) return
    conversationMonitorStarted = true

    const notify = () => window.setTimeout(() => reportConversationUpdate(), 0)
    const originalPushState = history.pushState
    const originalReplaceState = history.replaceState

    history.pushState = function pushState(...args) {
      const result = originalPushState.apply(this, args)
      notify()
      return result
    }

    history.replaceState = function replaceState(...args) {
      const result = originalReplaceState.apply(this, args)
      notify()
      return result
    }

    window.addEventListener('popstate', notify)
    window.addEventListener('hashchange', notify)
    notify()
  }

  return { reportConversationUpdate, start }
}
