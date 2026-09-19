import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_CUSTOM_ROLE_TEMPLATES } from '../group/defaultCustomRoleTemplates'
import { createDefaultStore } from '../group/store'
import type { WebAgentStore } from '../group/types'

const defaultCustomTemplateIds = DEFAULT_CUSTOM_ROLE_TEMPLATES.map(template => template.id)

describe('background role handlers', () => {
  it('exposes template and role routes and creates a role template through injected dependencies', async () => {
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

    const { ROLE_ROUTE_TYPES, createRoleHandlers } = await import('./roleHandlers')
    const broadcastStoreUpdated = vi.fn()
    const routes = createRoleHandlers({
      broadcastStoreUpdated,
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(),
        removeRole: vi.fn(),
      },
      sendPrompt: vi.fn(),
    })

    expect(ROLE_ROUTE_TYPES).toEqual([
      'ROLE_TEMPLATE_PERSONA_GENERATE',
      'ROLE_TEMPLATE_CREATE',
      'ROLE_TEMPLATE_UPDATE',
      'ROLE_TEMPLATE_DELETE',
      'GROUP_ROLE_CREATE',
      'GROUP_ROLES_CREATE_BATCH',
      'GROUP_ROLE_UPDATE',
      'GROUP_ROLE_DELETE',
      'GROUP_ROLE_RECOVER',
      'GROUP_ROLE_REINITIALIZE',
    ])
    expect(routes.map(route => route.type)).toEqual(ROLE_ROUTE_TYPES)

    const createTemplateRoute = routes.find(route => route.type === 'ROLE_TEMPLATE_CREATE')
    const response = await createTemplateRoute?.handler({
      type: 'ROLE_TEMPLATE_CREATE',
      name: '工程师',
      systemPrompt: '从工程角度分析',
      defaultChatSite: 'chatgpt',
    }, {})

    expect(response).toMatchObject({
      ok: true,
      template: {
        id: 'template-1',
        name: '工程师',
        systemPrompt: '从工程角度分析',
        defaultChatSite: 'chatgpt',
      },
    })
    expect(draftStore?.roleTemplateOrder).toEqual([...defaultCustomTemplateIds, 'template-1'])
    expect(broadcastStoreUpdated).toHaveBeenCalledWith(expect.objectContaining({
      roleTemplatesById: expect.objectContaining({ 'template-1': expect.any(Object) }),
    }))
  })

  it('stores a Grok project URL when creating a Grok template through the route', async () => {
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

    const { createRoleHandlers } = await import('./roleHandlers')
    const routes = createRoleHandlers({
      broadcastStoreUpdated: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(),
        removeRole: vi.fn(),
      },
      sendPrompt: vi.fn(),
    })

    const createTemplateRoute = routes.find(route => route.type === 'ROLE_TEMPLATE_CREATE')
    const response = await createTemplateRoute?.handler({
      type: 'ROLE_TEMPLATE_CREATE',
      name: '项目顾问',
      systemPrompt: '在项目上下文中回应',
      defaultChatSite: 'grok',
      grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81?source=share',
    }, {})

    expect(response).toMatchObject({
      ok: true,
      template: {
        id: 'template-1',
        name: '项目顾问',
        defaultChatSite: 'grok',
        grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81',
      },
    })
    expect(draftStore?.roleTemplatesById['template-1']).toMatchObject({
      grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81',
    })
  })

  it('skips the Web Agent persona when reinitializing a ChatGPT GPTs role', async () => {
    vi.resetModules()
    const startingStore = createDefaultStore()
    startingStore.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '方案讨论',
      mode: 'independent',
      roleIds: ['role-1'],
      messageIds: [],
      nextMessageSeq: 1,
      status: 'ready',
      createdAt: 1,
      updatedAt: 1,
    }
    startingStore.rolesById['role-1'] = {
      id: 'role-1',
      chatId: 'chat-1',
      name: '工程师',
      systemPrompt: '从工程角度分析',
      chatSite: 'chatgpt',
      chatGptGptsUrl: 'https://chatgpt.com/g/g-coach',
      status: 'ready',
      contextCursor: 0,
      createdAt: 1,
      updatedAt: 1,
    }
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const result = await mutator(startingStore)
          return { store: startingStore, result }
        }),
      }
    })

    const { createRoleHandlers } = await import('./roleHandlers')
    const sendPrompt = vi.fn()
    const routes = createRoleHandlers({
      broadcastStoreUpdated: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(() => ({ chatId: 'chat-1', roleId: 'role-1', tabId: 101, frameId: 7, ready: true, lastSeenAt: 0 })),
        removeRole: vi.fn(),
      },
      sendPrompt,
    })

    const reinitializeRoute = routes.find(route => route.type === 'GROUP_ROLE_REINITIALIZE')
    await reinitializeRoute?.handler({ type: 'GROUP_ROLE_REINITIALIZE', chatId: 'chat-1', roleId: 'role-1' }, {})

    expect(sendPrompt).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.objectContaining({
        includesPersona: false,
        content: expect.not.stringContaining('从工程角度分析'),
      }),
    }))
  })

  it('closes the removed person frame without deleting historical messages', async () => {
    vi.resetModules()
    const startingStore = createDefaultStore()
    startingStore.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '方案讨论',
      mode: 'independent',
      roleIds: ['role-1'],
      messageIds: ['msg-1'],
      nextMessageSeq: 2,
      status: 'ready',
      createdAt: 1,
      updatedAt: 1,
    }
    startingStore.rolesById['role-1'] = {
      id: 'role-1',
      chatId: 'chat-1',
      name: '工程师',
      status: 'ready',
      contextCursor: 0,
      createdAt: 1,
      updatedAt: 1,
    }
    startingStore.messagesById['msg-1'] = {
      id: 'msg-1',
      chatId: 'chat-1',
      seq: 1,
      type: 'assistant',
      content: '历史观点',
      roleId: 'role-1',
      roleName: '工程师',
      createdAt: 1,
      status: 'received',
    }
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const store = structuredClone(startingStore)
          const result = await mutator(store)
          return { store, result }
        }),
      }
    })

    const removeRole = vi.fn()
    const broadcastStoreUpdated = vi.fn()
    const { createRoleHandlers } = await import('./roleHandlers')
    const routes = createRoleHandlers({
      broadcastStoreUpdated,
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(),
        removeRole,
      },
      sendPrompt: vi.fn(),
    })

    const deleteRoute = routes.find(route => route.type === 'GROUP_ROLE_DELETE')
    const response = await deleteRoute?.handler({ type: 'GROUP_ROLE_DELETE', roleId: 'role-1' }, {}) as { store?: WebAgentStore } | undefined

    expect(removeRole).toHaveBeenCalledWith('chat-1', 'role-1')
    expect(response?.store?.chatsById['chat-1'].roleIds).toEqual([])
    expect(response?.store?.messagesById['msg-1']).toMatchObject({
      roleId: 'role-1',
      roleName: '工程师',
      content: '历史观点',
    })
    expect(broadcastStoreUpdated).toHaveBeenCalledWith(expect.objectContaining({
      messagesById: expect.objectContaining({ 'msg-1': expect.any(Object) }),
    }))
  })

  it('generates a role template persona using the first configured external model', async () => {
    vi.resetModules()
    const startingStore = createDefaultStore()
    startingStore.settings.externalModelOrder = ['model-first', 'model-second']
    startingStore.settings.externalModelsById = {
      'model-first': {
        id: 'model-first',
        name: '主力 API',
        format: 'openai',
        baseUrl: 'https://api.first.example/v1',
        apiKey: 'sk-first',
        modelName: 'first-chat-model',
        createdAt: 1,
        updatedAt: 1,
      },
      'model-second': {
        id: 'model-second',
        name: '备用 API',
        format: 'openai',
        baseUrl: 'https://api.second.example/v1',
        apiKey: 'sk-second',
        modelName: 'second-chat-model',
        createdAt: 1,
        updatedAt: 1,
      },
    }
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const result = await mutator(startingStore)
          return { store: startingStore, result }
        }),
      }
    })

    const externalComplete = vi.fn(async () => ({
      content: JSON.stringify({
        name: '增长顾问',
        description: '负责从获客、转化和复盘角度给建议。',
        systemPrompt: '你是增长顾问。先判断目标和约束，再给出可执行建议。',
      }),
    }))
    const { createRoleHandlers } = await import('./roleHandlers')
    const routes = createRoleHandlers({
      broadcastStoreUpdated: vi.fn(),
      externalModelClient: { complete: externalComplete },
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(),
        removeRole: vi.fn(),
      },
      sendPrompt: vi.fn(),
    })

    const generateRoute = routes.find(route => route.type === 'ROLE_TEMPLATE_PERSONA_GENERATE')!
    const response = await generateRoute.handler({
      type: 'ROLE_TEMPLATE_PERSONA_GENERATE',
      description: '一个擅长小红书增长的内容顾问',
    }, {})

    expect(externalComplete).toHaveBeenCalledWith({
      model: startingStore.settings.externalModelsById['model-first'],
      prompt: expect.stringContaining('一个擅长小红书增长的内容顾问'),
    })
    expect(response).toMatchObject({
      ok: true,
      persona: {
        name: '增长顾问',
        description: '负责从获客、转化和复盘角度给建议。',
        systemPrompt: '你是增长顾问。先判断目标和约束，再给出可执行建议。',
      },
    })
  })

  it('creates a role template and group role that target an external model', async () => {
    vi.resetModules()
    const startingStore = createDefaultStore()
    startingStore.settings.externalModelOrder = ['model-1']
    startingStore.settings.externalModelsById = {
      'model-1': {
        id: 'model-1',
        name: '本地模型',
        format: 'openai',
        baseUrl: 'https://api.example.test/v1',
        apiKey: 'sk-test',
        modelName: 'local-chat-model',
        createdAt: 1,
        updatedAt: 1,
      },
    }
    startingStore.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '方案讨论',
      mode: 'independent',
      roleIds: [],
      messageIds: [],
      nextMessageSeq: 1,
      status: 'ready',
      createdAt: 1,
      updatedAt: 1,
    }

    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const result = await mutator(startingStore)
          return { store: startingStore, result }
        }),
      }
    })

    const { createRoleHandlers } = await import('./roleHandlers')
    const routes = createRoleHandlers({
      broadcastStoreUpdated: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
      runtimeFrames: {
        getByRole: vi.fn(),
        removeRole: vi.fn(),
      },
      sendPrompt: vi.fn(),
    })

    const createTemplateRoute = routes.find(route => route.type === 'ROLE_TEMPLATE_CREATE')!
    await createTemplateRoute.handler({
      type: 'ROLE_TEMPLATE_CREATE',
      name: '工程师',
      systemPrompt: '从工程角度分析',
      defaultModelSource: 'external',
      defaultExternalModelId: 'model-1',
    }, {})

    expect(startingStore.roleTemplatesById['template-1']).toMatchObject({
      defaultModelSource: 'external',
      defaultExternalModelId: 'model-1',
    })

    const createRoleRoute = routes.find(route => route.type === 'GROUP_ROLE_CREATE')!
    const response = await createRoleRoute.handler({
      type: 'GROUP_ROLE_CREATE',
      chatId: 'chat-1',
      roleTemplateId: 'template-1',
    }, {})

    expect(response).toMatchObject({
      ok: true,
      role: {
        modelSource: 'external',
        externalModelId: 'model-1',
      },
    })
  })
})
