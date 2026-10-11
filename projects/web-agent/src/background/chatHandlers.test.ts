import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { WebAgentStore } from '../group/types'

describe('background chat handlers', () => {
  it('exposes group chat routes and creates a chat through the injected runtime dependencies', async () => {
    vi.resetModules()
    let draftStore: WebAgentStore | undefined
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const store = createDefaultStore()
          const result = await mutator(store)
          draftStore = store
          return { store, result }
        }),
      }
    })

    const { CHAT_ROUTE_TYPES, createChatHandlers } = await import('./chatHandlers')
    const broadcastStoreUpdated = vi.fn()
    const routes = createChatHandlers({
      broadcastStoreUpdated,
      getChatStatusFromRoles: () => 'ready',
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: { removeRole: vi.fn() },
    })

    expect(CHAT_ROUTE_TYPES).toEqual([
      'GROUP_CHAT_CREATE',
      'GROUP_CHAT_DUPLICATE',
      'GROUP_CHAT_SWITCH',
      'GROUP_CHAT_UPDATE',
      'GROUP_CHAT_DELETE',
      'GROUP_CHAT_CLEAR_MESSAGES',
      'GROUP_CHAT_CLOSE',
      'GROUP_CHAT_MARK_READ',
    ])
    expect(routes.map(route => route.type)).toEqual(CHAT_ROUTE_TYPES)

    const createRoute = routes.find(route => route.type === 'GROUP_CHAT_CREATE')
    const response = await createRoute?.handler({ type: 'GROUP_CHAT_CREATE', name: '架构评审' }, {})

    expect(response).toMatchObject({
      ok: true,
      chat: { id: 'chat-1', name: '架构评审', status: 'draft', requireManualMention: true },
    })
    expect(draftStore?.currentChatId).toBe('chat-1')
    expect(broadcastStoreUpdated).toHaveBeenCalledWith(expect.objectContaining({ currentChatId: 'chat-1' }))
  })

  it('creates a chat with the people supplied by a group template', async () => {
    vi.resetModules()
    const { BUILTIN_GROUP_TEMPLATES, buildBuiltinGroupTemplateWelcomeMessage } = await import('../group/builtinGroupTemplates')
    const template = BUILTIN_GROUP_TEMPLATES[0]
    let draftStore: WebAgentStore | undefined
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const store = createDefaultStore()
          const result = await mutator(store)
          draftStore = store
          return { store, result }
        }),
      }
    })

    const idCounters: Record<string, number> = {}
    const { createChatHandlers } = await import('./chatHandlers')
    const routes = createChatHandlers({
      broadcastStoreUpdated: vi.fn(),
      getChatStatusFromRoles: () => 'ready',
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => {
        idCounters[prefix] = (idCounters[prefix] ?? 0) + 1
        return `${prefix}-${idCounters[prefix]}`
      }),
      now: vi.fn(() => 100),
      runtimeFrames: { removeRole: vi.fn() },
    })

    const createRoute = routes.find(route => route.type === 'GROUP_CHAT_CREATE')
    const response = await createRoute?.handler({
      type: 'GROUP_CHAT_CREATE',
      name: template.defaultChatName,
      mode: template.defaultMode,
      roles: template.roles,
      welcomeMessage: buildBuiltinGroupTemplateWelcomeMessage(template),
    }, {})

    expect(response).toMatchObject({
      ok: true,
      chat: {
        id: 'chat-1',
        name: template.defaultChatName,
        mode: template.defaultMode,
        status: 'initializing',
      },
    })
    expect(draftStore?.chatsById['chat-1'].roleIds).toHaveLength(template.roles.length)
    expect(draftStore?.chatsById['chat-1'].messageIds).toEqual(['msg-1'])
    expect(draftStore?.chatsById['chat-1'].nextMessageSeq).toBe(2)
    expect(draftStore?.messagesById['msg-1']).toMatchObject({
      chatId: 'chat-1',
      seq: 1,
      type: 'assistant',
      contentFormat: 'markdown',
      roleName: template.roles[0].name,
      content: expect.stringContaining(`欢迎来到「${template.name}」`),
      status: 'received',
    })
    expect(Object.values(draftStore?.rolesById ?? {}).map(role => ({
      name: role.name,
      description: role.description,
      systemPrompt: role.systemPrompt,
      chatSite: role.chatSite,
    }))).toEqual(template.roles.map(role => ({
      name: role.name,
      description: role.description,
      systemPrompt: role.systemPrompt,
      chatSite: 'deepseek',
    })))
  })
})
