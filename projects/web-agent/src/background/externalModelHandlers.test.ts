import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { WebAgentStore } from '../group/types'

describe('background external model handlers', () => {
  it('creates, updates, and prevents deleting an external model in use', async () => {
    vi.resetModules()
    const currentStore = createDefaultStore()
    vi.doMock('./storeAccess', async importOriginal => {
      const actual = await importOriginal<typeof import('./storeAccess')>()
      return {
        ...actual,
        mutateStore: vi.fn(async (mutator: (store: WebAgentStore) => unknown) => {
          const result = await mutator(currentStore)
          return { store: currentStore, result }
        }),
      }
    })

    const { EXTERNAL_MODEL_ROUTE_TYPES, createExternalModelHandlers } = await import('./externalModelHandlers')
    const routes = createExternalModelHandlers({
      broadcastStoreUpdated: vi.fn(),
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
    })

    expect(EXTERNAL_MODEL_ROUTE_TYPES).toEqual([
      'EXTERNAL_MODEL_CREATE',
      'EXTERNAL_MODEL_UPDATE',
      'EXTERNAL_MODEL_DELETE',
      'EXTERNAL_MODEL_TEST',
    ])
    expect(routes.map(route => route.type)).toEqual(EXTERNAL_MODEL_ROUTE_TYPES)

    const create = routes.find(route => route.type === 'EXTERNAL_MODEL_CREATE')!
    await create.handler({
      type: 'EXTERNAL_MODEL_CREATE',
      name: '本地模型',
      format: 'openai',
      baseUrl: 'https://api.example.test/v1',
      apiKey: 'sk-test',
      modelName: 'local-chat-model',
    }, {})

    expect(currentStore.settings.externalModelOrder).toEqual(['external-model-1'])
    expect(currentStore.settings.externalModelsById['external-model-1']).toMatchObject({
      name: '本地模型',
      format: 'openai',
      modelName: 'local-chat-model',
    })

    const update = routes.find(route => route.type === 'EXTERNAL_MODEL_UPDATE')!
    await update.handler({
      type: 'EXTERNAL_MODEL_UPDATE',
      modelId: 'external-model-1',
      name: 'Claude 代理',
      format: 'anthropic',
      baseUrl: 'https://api.anthropic.example',
      apiKey: 'sk-next',
      modelName: 'claude-sonnet',
    }, {})

    expect(currentStore.settings.externalModelsById['external-model-1']).toMatchObject({
      name: 'Claude 代理',
      format: 'anthropic',
      baseUrl: 'https://api.anthropic.example',
      apiKey: 'sk-next',
      modelName: 'claude-sonnet',
      updatedAt: 100,
    })

    currentStore.rolesById['role-1'] = {
      id: 'role-1',
      chatId: 'chat-1',
      name: '工程师',
      modelSource: 'external',
      externalModelId: 'external-model-1',
      status: 'ready',
      contextCursor: 0,
      createdAt: 1,
      updatedAt: 1,
    }
    const remove = routes.find(route => route.type === 'EXTERNAL_MODEL_DELETE')!
    await expect(remove.handler({ type: 'EXTERNAL_MODEL_DELETE', modelId: 'external-model-1' }, {})).rejects.toThrow('外部模型正在被人员使用')
  })

  it('tests a saved external model with a lightweight completion prompt', async () => {
    vi.resetModules()
    const currentStore = createDefaultStore()
    currentStore.settings.externalModelOrder = ['external-model-1']
    currentStore.settings.externalModelsById['external-model-1'] = {
      id: 'external-model-1',
      name: '本地模型',
      format: 'openai',
      baseUrl: 'https://api.example.test/v1',
      apiKey: 'sk-test',
      modelName: 'local-chat-model',
      createdAt: 1,
      updatedAt: 1,
    }
    vi.doMock('../group/store', async importOriginal => {
      const actual = await importOriginal<typeof import('../group/store')>()
      return {
        ...actual,
        loadStore: vi.fn(async () => currentStore),
      }
    })

    const complete = vi.fn(async () => ({ content: 'OK' }))
    const { EXTERNAL_MODEL_ROUTE_TYPES, createExternalModelHandlers } = await import('./externalModelHandlers')
    const routes = createExternalModelHandlers({
      broadcastStoreUpdated: vi.fn(),
      externalModelClient: { complete },
      newId: vi.fn((prefix: string) => `${prefix}-1`),
      now: vi.fn(() => 100),
    })

    expect(EXTERNAL_MODEL_ROUTE_TYPES).toContain('EXTERNAL_MODEL_TEST')

    const test = routes.find(route => route.type === 'EXTERNAL_MODEL_TEST')!
    await expect(test.handler({ type: 'EXTERNAL_MODEL_TEST', modelId: 'external-model-1' }, {})).resolves.toMatchObject({
      ok: true,
      content: 'OK',
    })
    expect(complete).toHaveBeenCalledWith({
      model: currentStore.settings.externalModelsById['external-model-1'],
      prompt: expect.stringContaining('Web Agent'),
    })
  })
})
