import type { FrameRoleReadyResponse, RoleToBackgroundMessage } from '../group/runtimeProtocol'
import type { ContentLogger } from './runtimeClient'
import type { RoleSession } from './roleSession'
import type { ChatSiteAdapter } from './sites/types'

const FRAME_ASSIGN_MESSAGE = 'WEB_AGENT_ASSIGN_FRAME_ROLE'

export interface FrameRoleHandshakeOptions {
  siteAdapter: ChatSiteAdapter
  roleSession: RoleSession
  log: ContentLogger
  seedStoredRoleReplies(replies: string[] | undefined): void
  sendRuntimeMessage<T>(message: RoleToBackgroundMessage): Promise<T>
}

export function performFrameRoleAssignment(options: FrameRoleHandshakeOptions, chatId: string, roleId: string, hostTabId?: number): void {
  if (!roleId) return

  const snapshot = options.siteAdapter.getConversationSnapshot()
  options.log.info('frame-role:assignment-received', { chatId, roleId, hostTabId, conversationId: snapshot.conversationId })
  options
    .sendRuntimeMessage<FrameRoleReadyResponse>({
      type: 'TEAM_FRAME_ROLE_READY',
      chatId,
      roleId,
      hostTabId,
      conversationId: snapshot.conversationId || options.siteAdapter.getConversationId(),
      conversationUrl: snapshot.conversationUrl,
    })
    .then(response => {
      if (!response.ok || !response.role) {
        options.log.warn('frame-role:ready-failed', { roleId, error: response.error })
        return
      }

      options.seedStoredRoleReplies(response.replyHistory)
      options.roleSession.assignRole({
        chatId,
        roleId: response.role.id,
        roleName: response.role.name,
        roomId: response.role.chatId || chatId,
      })
    })
    .catch(error => options.log.warn('frame-role:ready-error', { roleId, error: error instanceof Error ? error.message : String(error) }))
}

export function registerFrameRoleHandshake(options: FrameRoleHandshakeOptions): void {
  window.addEventListener('message', event => {
    if (!event.data || typeof event.data !== 'object') return
    if (event.data.type !== FRAME_ASSIGN_MESSAGE) return

    const chatId = typeof event.data.chatId === 'string' ? event.data.chatId : typeof event.data.roomId === 'string' ? event.data.roomId : ''
    const roleId = typeof event.data.roleId === 'string' ? event.data.roleId : ''
    const hostTabId = typeof event.data.hostTabId === 'number' ? event.data.hostTabId : undefined
    performFrameRoleAssignment(options, chatId, roleId, hostTabId)
  })
}

export interface RuntimeFrameAssignmentMessage {
  type: typeof FRAME_ASSIGN_MESSAGE
  chatId?: string
  roomId?: string
  roleId?: string
  hostTabId?: number
}

export function handleRuntimeFrameAssignment(options: FrameRoleHandshakeOptions, message: RuntimeFrameAssignmentMessage): void {
  const chatId = typeof message.chatId === 'string' ? message.chatId : typeof message.roomId === 'string' ? message.roomId : ''
  const roleId = typeof message.roleId === 'string' ? message.roleId : ''
  performFrameRoleAssignment(options, chatId, roleId, message.hostTabId)
}
