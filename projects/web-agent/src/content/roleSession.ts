import type { ChatSiteAdapter } from './sites/types'
import type { ContentLogger } from './runtimeClient'

export interface AssignedRole {
  chatId: string
  roleId: string
  roleName?: string
  roomId?: string
}

export interface ActivePrompt {
  messageId: string
  replyAttemptId?: string
}

export interface RoleSession {
  getAssignedRole(): AssignedRole | null
  getActivePrompt(): ActivePrompt | undefined
  getActiveMessageId(): string | undefined
  getActiveReplyAttemptId(): string | undefined
  getAssignedChatId(role?: AssignedRole | null): string
  assignRole(role: AssignedRole): void
  startPrompt(messageId: string, replyAttemptId?: string): void
  clearActivePrompt(messageId?: string): string | undefined
}

export function createRoleSession(options: {
  siteAdapter: ChatSiteAdapter
  log: ContentLogger
  onAssigned?: (role: AssignedRole) => void
}): RoleSession {
  let assignedRole: AssignedRole | null = null
  let activeMessageId: string | undefined
  let activeReplyAttemptId: string | undefined

  const getAssignedChatId = (role = assignedRole): string => role?.chatId || role?.roomId || ''

  return {
    getAssignedRole: () => assignedRole,
    getActivePrompt: () => (activeMessageId ? { messageId: activeMessageId, replyAttemptId: activeReplyAttemptId } : undefined),
    getActiveMessageId: () => activeMessageId,
    getActiveReplyAttemptId: () => activeReplyAttemptId,
    getAssignedChatId,
    assignRole(role): void {
      assignedRole = role
      activeMessageId = undefined
      activeReplyAttemptId = undefined
      options.onAssigned?.(role)
      options.log.info('role-assigned', {
        chatId: getAssignedChatId(role),
        roleId: role.roleId,
        roleName: role.roleName,
        roomId: role.roomId,
        conversationId: options.siteAdapter.getConversationId(),
      })
    },
    startPrompt(messageId: string, replyAttemptId?: string): void {
      activeMessageId = messageId
      activeReplyAttemptId = replyAttemptId
    },
    clearActivePrompt(messageId?: string): string | undefined {
      if (messageId && activeMessageId !== messageId) return activeReplyAttemptId

      const replyAttemptId = activeReplyAttemptId
      activeMessageId = undefined
      activeReplyAttemptId = undefined
      return replyAttemptId
    },
  }
}
