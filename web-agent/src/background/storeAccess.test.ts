import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupChat, GroupMessage, GroupRole } from '../group/types'

describe('background store access helpers', () => {
  it('reads chat-scoped roles and messages while preserving stored order', async () => {
    const { getChatMessages, getChatRoles, requireChat, requireRole } = await import('./storeAccess')
    const store = createDefaultStore()
    const chat: GroupChat = {
      id: 'chat-1',
      name: '架构评审',
      mode: 'independent',
      roleIds: ['role-missing', 'role-1', 'role-2'],
      messageIds: ['msg-2', 'msg-missing', 'msg-1'],
      nextMessageSeq: 3,
      status: 'ready',
      createdAt: 1,
      updatedAt: 1,
    }
    const roleOne: GroupRole = {
      id: 'role-1',
      chatId: chat.id,
      name: '工程师',
      status: 'ready',
      contextCursor: 0,
      createdAt: 1,
      updatedAt: 1,
    }
    const roleTwo: GroupRole = { ...roleOne, id: 'role-2', name: '产品经理' }
    const messageOne: GroupMessage = {
      id: 'msg-1',
      chatId: chat.id,
      seq: 1,
      type: 'user',
      content: '先看架构',
      createdAt: 1,
      status: 'received',
    }
    const messageTwo: GroupMessage = { ...messageOne, id: 'msg-2', seq: 2, content: '再看风险' }

    store.chatsById[chat.id] = chat
    store.rolesById[roleOne.id] = roleOne
    store.rolesById[roleTwo.id] = roleTwo
    store.messagesById[messageOne.id] = messageOne
    store.messagesById[messageTwo.id] = messageTwo

    expect(requireChat(store, 'chat-1')).toBe(chat)
    expect(requireRole(store, 'chat-1', 'role-1')).toBe(roleOne)
    expect(getChatRoles(store, chat).map(role => role.id)).toEqual(['role-1', 'role-2'])
    expect(getChatMessages(store, chat).map(message => message.id)).toEqual(['msg-2', 'msg-1'])
    expect(() => requireChat(store, 'missing')).toThrow('找不到群聊：missing')
    expect(() => requireRole(store, 'chat-1', 'role-missing')).toThrow('找不到人员：role-missing')
  })

  it('wraps queued store mutations with the updated store and mutator result', async () => {
    vi.resetModules()
    const updateStoreQueued = vi.fn(async (mutator: (store: ReturnType<typeof createDefaultStore>) => unknown) => {
      const store = createDefaultStore()
      return mutator(store)
    })
    vi.doMock('../group/store', async importOriginal => ({
      ...(await importOriginal<typeof import('../group/store')>()),
      updateStoreQueued,
    }))
    const { mutateStore } = await import('./storeAccess')

    const mutation = await mutateStore(store => {
      store.currentChatId = 'chat-1'
      return 'changed'
    })

    expect(updateStoreQueued).toHaveBeenCalledTimes(1)
    expect(mutation.result).toBe('changed')
    expect(mutation.store.currentChatId).toBe('chat-1')
  })
})
