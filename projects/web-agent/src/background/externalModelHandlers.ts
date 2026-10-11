import type { ExternalModelConfig, ExternalModelFormat, WebAgentStore } from '../group/types'
import { loadStore } from '../group/store'
import type { ExternalModelClient } from './externalModelClient'
import type { BackgroundMessageRoute } from './messageRouter'
import type { RuntimeMessage } from './runtimeClient'
import { mutateStore } from './storeAccess'

export const EXTERNAL_MODEL_ROUTE_TYPES = [
  'EXTERNAL_MODEL_CREATE',
  'EXTERNAL_MODEL_UPDATE',
  'EXTERNAL_MODEL_DELETE',
  'EXTERNAL_MODEL_TEST',
] as const

export interface ExternalModelHandlersDependencies {
  broadcastStoreUpdated(store: WebAgentStore, excludeTabId?: number): Promise<void> | void
  externalModelClient?: ExternalModelClient
  newId(prefix: string): string
  now(): number
}

export function createExternalModelHandlers(deps: ExternalModelHandlersDependencies): BackgroundMessageRoute[] {
  const handleCreate = async (message: RuntimeMessage) => {
    const timestamp = deps.now()
    const model = readExternalModelInput(message, deps.newId('external-model'), timestamp, timestamp)
    const { store } = await mutateStore(store => {
      store.settings.externalModelsById[model.id] = model
      if (!store.settings.externalModelOrder.includes(model.id)) store.settings.externalModelOrder.push(model.id)
    })
    await deps.broadcastStoreUpdated(store)
    return { ok: true, model, store }
  }

  const handleUpdate = async (message: RuntimeMessage) => {
    const modelId = requireString(message.modelId, '缺少外部模型 ID')
    const timestamp = deps.now()
    const { store, result } = await mutateStore(store => {
      const existing = store.settings.externalModelsById[modelId]
      if (!existing) throw new Error(`找不到外部模型：${modelId}`)
      const model = readExternalModelInput(message, modelId, existing.createdAt, timestamp)
      store.settings.externalModelsById[modelId] = model
      if (!store.settings.externalModelOrder.includes(modelId)) store.settings.externalModelOrder.push(modelId)
      return model
    })
    await deps.broadcastStoreUpdated(store)
    return { ok: true, model: result, store }
  }

  const handleDelete = async (message: RuntimeMessage) => {
    const modelId = requireString(message.modelId, '缺少外部模型 ID')
    const { store } = await mutateStore(store => {
      if (Object.values(store.rolesById).some(role => role.modelSource === 'external' && role.externalModelId === modelId)) {
        throw new Error('外部模型正在被人员使用，不能删除')
      }
      delete store.settings.externalModelsById[modelId]
      store.settings.externalModelOrder = store.settings.externalModelOrder.filter(id => id !== modelId)
    })
    await deps.broadcastStoreUpdated(store)
    return { ok: true, store }
  }

  const handleTest = async (message: RuntimeMessage) => {
    if (!deps.externalModelClient) throw new Error('外部模型测试客户端不可用')
    const modelId = requireString(message.modelId, '缺少外部模型 ID')
    const store = await loadStore()
    const model = store.settings.externalModelsById[modelId]
    if (!model) throw new Error(`找不到外部模型：${modelId}`)
    const completion = await deps.externalModelClient.complete({
      model,
      prompt: '你是 Web Agent 的外部模型连通性测试。请只回复 OK。',
    })
    return { ok: true, content: completion.content }
  }

  return [
    { type: 'EXTERNAL_MODEL_CREATE', handler: handleCreate },
    { type: 'EXTERNAL_MODEL_UPDATE', handler: handleUpdate },
    { type: 'EXTERNAL_MODEL_DELETE', handler: handleDelete },
    { type: 'EXTERNAL_MODEL_TEST', handler: handleTest },
  ]
}

function readExternalModelInput(message: RuntimeMessage, id: string, createdAt: number, updatedAt: number): ExternalModelConfig {
  return {
    id,
    name: requireString(message.name, '外部模型名称不能为空'),
    format: readExternalModelFormat(message.format),
    baseUrl: requireString(message.baseUrl, '模型地址不能为空').replace(/\/+$/, ''),
    apiKey: requireString(message.apiKey, '模型 Key 不能为空'),
    modelName: requireString(message.modelName, '模型名称不能为空'),
    createdAt,
    updatedAt,
  }
}

function readExternalModelFormat(value: unknown): ExternalModelFormat {
  if (value === 'openai' || value === 'anthropic') return value
  throw new Error('外部模型格式必须是 OpenAI 或 Anthropic')
}

function requireString(value: unknown, error: string): string {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  if (!trimmed) throw new Error(error)
  return trimmed
}
