import { updateStoreQueued } from '../group/store'
import type { GroupChat, GroupMessage, GroupRole, WebAgentStore } from '../group/types'

export interface StoreMutationResult<T> {
  store: WebAgentStore
  result: T
}

export async function mutateStore<T>(mutator: (store: WebAgentStore) => T | Promise<T>): Promise<StoreMutationResult<T>> {
  return updateStoreQueued(async store => {
    const result = await mutator(store)
    return { store, result }
  })
}

export function getChatRoles(store: WebAgentStore, chat: GroupChat): GroupRole[] {
  return chat.roleIds.map(roleId => store.rolesById[roleId]).filter((role): role is GroupRole => Boolean(role))
}

export function getChatMessages(store: WebAgentStore, chat: GroupChat): GroupMessage[] {
  return chat.messageIds.map(messageId => store.messagesById[messageId]).filter((message): message is GroupMessage => Boolean(message))
}

export function requireChat(store: WebAgentStore, chatId: unknown): GroupChat {
  if (typeof chatId !== 'string') throw new Error('缺少群聊 ID')
  const chat = store.chatsById[chatId]
  if (!chat) throw new Error(`找不到群聊：${chatId}`)
  return chat
}

export function requireRole(store: WebAgentStore, chatId: string, roleId: unknown): GroupRole {
  if (typeof roleId !== 'string') throw new Error('缺少人员 ID')
  const role = store.rolesById[roleId]
  if (!role || role.chatId !== chatId) throw new Error(`找不到人员：${roleId}`)
  return role
}
