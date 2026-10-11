import {
  DEFAULT_ORCHESTRATION_MAX_NODE_EXECUTIONS,
  DEFAULT_ORCHESTRATION_MAX_ROUNDS,
  MAX_ORCHESTRATION_MAX_NODE_EXECUTIONS,
  MAX_ORCHESTRATION_MAX_ROUNDS,
  type ChatSite,
  type GroupChat,
  type OrchestrationAutoPlanHistoryEntry,
  type GroupRole,
  type OrchestrationFlow,
  type OrchestrationGraphSnapshot,
  type OrchestrationStage,
  type WebAgentStore,
} from '../group/types'
import {
  buildAutoOrchestrationPrompt,
  buildAutoOrchestrationRepairPrompt,
  parseAutoOrchestrationPlan,
  type AutoOrchestrationPlan,
} from '../group/orchestrationAutoPlan'
import { createGroupRole, updateGroupRole, type GroupRoleInput } from '../group/roleTemplates'
import type { ExternalModelClient, ExternalModelCompletionInput } from './externalModelClient'
import type { BackgroundMessageRoute } from './messageRouter'
import { type RuntimeMessage } from './runtimeClient'
import { getChatRoles, mutateStore, requireChat } from './storeAccess'
import {
  type OrchestrationRuntimeDependencies,
  resumeOrchestrationRun,
  retryOrchestrationReview,
  retryOrchestrationStage,
  skipOrchestrationStage,
  startOrchestrationRun,
  stopOrchestrationRun,
} from './orchestrationRuntime'

export interface OrchestrationHandlersDependencies extends OrchestrationRuntimeDependencies {
  broadcastStoreUpdated(store: WebAgentStore, excludeTabId?: number): Promise<void> | void
  broadcastAutoGenerateStream?(message: OrchestrationAutoStreamMessage): Promise<void> | void
}

export interface OrchestrationAutoStreamMessage {
  type: 'GROUP_ORCHESTRATION_AUTO_STREAM_CHUNK'
  streamId: string
  chunk: string
  content: string
}

export function createOrchestrationHandlers(deps: OrchestrationHandlersDependencies): BackgroundMessageRoute[] {
  const handleFlowSave = async (message: RuntimeMessage) => {
    const flow = requireFlowPayload(message.flow)
    const { store, result } = await persistFlowDraft(deps, flow)
    await deps.broadcastStoreUpdated(store)
    return { ok: true, flow: result, store }
  }

  const handleFlowDelete = async (message: RuntimeMessage) => {
    const chatId = requireString(message.chatId, '缺少群聊 ID')
    const flowId = requireString(message.flowId, '缺少编排流程 ID')
    const { store } = await mutateStore(store => {
      requireChat(store, chatId)
      delete store.orchestrationFlowsById[flowId]
      store.orchestrationFlowOrderByChatId[chatId] = (store.orchestrationFlowOrderByChatId[chatId] ?? []).filter(id => id !== flowId)
    })
    await deps.broadcastStoreUpdated(store)
    return { ok: true, store }
  }

  const handleAutoGenerate = async (message: RuntimeMessage) => {
    const chatId = requireString(message.chatId, '缺少群聊 ID')
    const task = requireString(message.task, '自动编排任务不能为空')
    const instruction = readOptionalString(message.instruction) ?? task
    const flowId = readOptionalString(message.flowId)
    const currentFlow = isRecord(message.flow) ? requireFlowPayload(message.flow) : undefined
    const history = readAutoPlanHistory(message.history ?? currentFlow?.autoPlanHistory)
    const snapshot = await mutateStore(store => {
      const chat = requireChat(store, chatId)
      const roles = getChatRoles(store, chat)
      const plannerModelId = readOptionalString(message.plannerModelId) ?? store.settings.externalModelOrder[0]
      const model = plannerModelId ? store.settings.externalModelsById[plannerModelId] : undefined
      if (!model) throw new Error('请先配置外部模型后再使用自动编排')
      return { chat: { ...chat }, roles: roles.map(role => ({ ...role })), model, store: structuredClone(store) as WebAgentStore }
    })

    const externalModelClient = deps.externalModelClient
    if (!externalModelClient) throw new Error('自动编排模型客户端不可用')
    const streamId = readOptionalString(message.streamId)
    const emitChunk = streamId
      ? (chunk: string, content: string) => deps.broadcastAutoGenerateStream?.({ type: 'GROUP_ORCHESTRATION_AUTO_STREAM_CHUNK', streamId, chunk, content })
      : undefined
    const prompt = buildAutoOrchestrationPrompt({ task, instruction, existingRoles: snapshot.result.roles, currentFlow, history, store: snapshot.result.store })
    const first = await streamAutoPlanCompletion(externalModelClient, { model: snapshot.result.model, prompt }, emitChunk)
    const existingRoleIds = new Set(snapshot.result.roles.map(role => role.id))
    let plan: AutoOrchestrationPlan
    let assistantContent = first.content
    try {
      plan = parseAutoOrchestrationPlan(first.content, existingRoleIds)
    } catch (error) {
      const repairPrompt = buildAutoOrchestrationRepairPrompt({
        task,
        instruction,
        existingRoles: snapshot.result.roles,
        currentFlow,
        history,
        store: snapshot.result.store,
        invalidOutput: first.content,
        error: error instanceof Error ? error.message : String(error),
      })
      const repaired = await streamAutoPlanCompletion(externalModelClient, { model: snapshot.result.model, prompt: repairPrompt }, emitChunk)
      assistantContent = repaired.content
      plan = parseAutoOrchestrationPlan(repaired.content, existingRoleIds)
    }

    const timestamp = deps.now()
    const nextHistory = appendAutoPlanHistory(history, instruction, plan, assistantContent, timestamp, deps.newId)
    const generated = await mutateStore(store => {
      const chat = requireChat(store, chatId)
      const existingRoles = getChatRoles(store, chat)
      const { roleIdsByKey, createdRoleIds, reusedRoleIds } = materializeAutoPlanRoles(store, chat, existingRoles, plan, deps.newId, timestamp)
      const flow = buildFlowFromAutoPlan(chat, flowId ?? deps.newId('flow'), task, plan, roleIdsByKey, nextHistory, timestamp)
      const persistedFlow = persistAutoGeneratedFlow(store, flow, timestamp)
      return { flow: persistedFlow, createdRoleIds, reusedRoleIds }
    })
    await deps.broadcastStoreUpdated(generated.store)
    return { ok: true, ...generated.result, store: generated.store }
  }

  return [
    { type: 'GROUP_ORCHESTRATION_FLOW_SAVE', handler: handleFlowSave },
    { type: 'GROUP_ORCHESTRATION_FLOW_DELETE', handler: handleFlowDelete },
    { type: 'GROUP_ORCHESTRATION_AUTO_GENERATE', handler: handleAutoGenerate },
    {
      type: 'GROUP_ORCHESTRATION_RUN',
      handler: async message => {
        const chatId = requireString(message.chatId, '缺少群聊 ID')
        const submittedFlow = isRecord(message.flow) ? requireFlowPayload(message.flow) : undefined
        if (submittedFlow && submittedFlow.chatId !== chatId) throw new Error('编排流程不属于当前群聊')
        const flowId = submittedFlow ? (await persistFlowDraft(deps, submittedFlow)).result.id : requireString(message.flowId, '缺少编排流程 ID')
        const response = await startOrchestrationRun(deps, {
          chatId,
          flowId,
          task: requireString(message.task ?? message.raw, '编排任务不能为空'),
          maxRounds: typeof message.maxRounds === 'number' ? message.maxRounds : submittedFlow?.maxRounds,
          maxNodeExecutions: typeof message.maxNodeExecutions === 'number' ? message.maxNodeExecutions : submittedFlow?.maxNodeExecutions,
        })
        return { ok: true, ...response }
      },
    },
    {
      type: 'GROUP_ORCHESTRATION_STOP',
      handler: async message => ({ ok: true, ...(await stopOrchestrationRun(deps, requireString(message.chatId, '缺少群聊 ID'))) }),
    },
    {
      type: 'GROUP_ORCHESTRATION_RESUME',
      handler: async message => ({ ok: true, ...(await resumeOrchestrationRun(deps, { chatId: requireString(message.chatId, '缺少群聊 ID'), runId: readOptionalString(message.runId) })) }),
    },
    {
      type: 'GROUP_ORCHESTRATION_RETRY_STAGE',
      handler: async message => ({ ok: true, ...(await retryOrchestrationStage(deps, requireString(message.chatId, '缺少群聊 ID'), readOptionalString(message.stageId))) }),
    },
    {
      type: 'GROUP_ORCHESTRATION_SKIP_STAGE',
      handler: async message => ({ ok: true, ...(await skipOrchestrationStage(deps, requireString(message.chatId, '缺少群聊 ID'), readOptionalString(message.stageId))) }),
    },
    {
      type: 'GROUP_ORCHESTRATION_RETRY_REVIEW',
      handler: async message => ({ ok: true, ...(await retryOrchestrationReview(deps, requireString(message.chatId, '缺少群聊 ID'))) }),
    },
  ]
}

function materializeAutoPlanRoles(
  store: WebAgentStore,
  chat: GroupChat,
  existingRoles: GroupRole[],
  plan: AutoOrchestrationPlan,
  newId: (prefix: string) => string,
  timestamp: number,
): { roleIdsByKey: Map<string, string>; createdRoleIds: string[]; reusedRoleIds: string[] } {
  const roleIdsByKey = new Map<string, string>()
  const createdRoleIds: string[] = []
  const reusedRoleIds: string[] = []
  for (const rolePlan of plan.roles) {
    const reusable = rolePlan.reuseRoleId ? store.rolesById[rolePlan.reuseRoleId] : findReusableGeneratedRole(existingRoles, rolePlan.name, rolePlan.preferredSite, store.settings.defaultChatSite)
    if (reusable && reusable.chatId === chat.id) {
      const patch: Partial<GroupRoleInput> = {}
      if (reusable.createdBy === 'orchestration-auto') {
        patch.name = rolePlan.name
        patch.description = rolePlan.description
        patch.systemPrompt = rolePlan.systemPrompt
        patch.modelSource = 'site'
        patch.chatSite = rolePlan.preferredSite
      } else if (rolePlan.preferredSiteExplicit && reusable.modelSource !== 'external') {
        patch.modelSource = 'site'
        patch.chatSite = rolePlan.preferredSite
      }
      if (Object.keys(patch).length > 0) updateGroupRole(store, reusable.id, patch, timestamp)
      roleIdsByKey.set(rolePlan.key, reusable.id)
      reusedRoleIds.push(reusable.id)
      continue
    }
    const role = createGroupRole(store, {
      chatId: chat.id,
      name: rolePlan.name,
      description: rolePlan.description,
      systemPrompt: rolePlan.systemPrompt,
      modelSource: 'site',
      chatSite: rolePlan.preferredSite,
    }, newId('role'), timestamp)
    role.createdBy = 'orchestration-auto'
    roleIdsByKey.set(rolePlan.key, role.id)
    createdRoleIds.push(role.id)
  }
  return { roleIdsByKey, createdRoleIds, reusedRoleIds: [...new Set(reusedRoleIds)] }
}

function persistAutoGeneratedFlow(store: WebAgentStore, flow: OrchestrationFlow, timestamp: number): OrchestrationFlow {
  const existing = store.orchestrationFlowsById[flow.id]
  const persisted: OrchestrationFlow = {
    ...flow,
    createdAt: existing?.createdAt ?? flow.createdAt ?? timestamp,
    updatedAt: timestamp,
  }
  store.orchestrationFlowsById[persisted.id] = persisted
  const order = store.orchestrationFlowOrderByChatId[persisted.chatId] ?? []
  store.orchestrationFlowOrderByChatId[persisted.chatId] = order.includes(persisted.id) ? order : [...order, persisted.id]
  return persisted
}

function findReusableGeneratedRole(existingRoles: GroupRole[], name: string, preferredSite: ChatSite, defaultChatSite: ChatSite): GroupRole | undefined {
  const normalizedName = name.trim().toLowerCase()
  return existingRoles.find(role => {
    const site = role.modelSource === 'external' ? undefined : role.chatSite ?? defaultChatSite
    return role.name.trim().toLowerCase() === normalizedName && site === preferredSite
  })
}

function buildFlowFromAutoPlan(
  chat: GroupChat,
  flowId: string,
  task: string,
  plan: AutoOrchestrationPlan,
  roleIdsByKey: Map<string, string>,
  autoPlanHistory: OrchestrationAutoPlanHistoryEntry[],
  timestamp: number,
): OrchestrationFlow {
  const stageIdByNodeId = new Map(plan.nodes.map(node => [node.id, `stage-${node.id}`]))
  const stages: OrchestrationStage[] = plan.nodes.map(node => {
    const roleId = roleIdsByKey.get(node.roleKey)
    if (!roleId) throw new Error(`自动编排节点缺少人员：${node.roleKey}`)
    const base = {
      id: stageIdByNodeId.get(node.id) ?? `stage-${node.id}`,
      kind: node.kind === 'review' ? 'review' as const : 'roles' as const,
      name: node.title,
      description: node.instruction,
      roleIds: [roleId],
    }
    if (node.kind !== 'review') return base
    if (!node.review) throw new Error(`自动编排审核节点缺少审核配置：${node.id}`)
    return {
      ...base,
      review: {
        reviewerRoleIds: [roleId],
        instructions: node.review.criteria,
        maxAttempts: node.review.maxAttempts,
        onMaxAttempts: node.review.onMaxAttempts,
      },
    }
  })
  const edges: OrchestrationGraphSnapshot['edges'] = plan.edges.map(edge => {
    const sourceStageId = stageIdByNodeId.get(edge.from)
    const targetStageId = stageIdByNodeId.get(edge.to)
    if (!sourceStageId || !targetStageId) throw new Error(`自动编排连线无效：${edge.from} -> ${edge.to}`)
    return {
      sourceStageId,
      targetStageId,
      ...(edge.branch ? { sourcePort: edge.branch } : {}),
    }
  })
  return {
    id: flowId,
    chatId: chat.id,
    name: plan.flowName || `${chat.name} 自动编排`,
    description: task || undefined,
    stages,
    graph: { stageNodes: stages, edges },
    autoPlanHistory,
    maxNodeExecutions: plan.maxNodeExecutions,
    maxRounds: plan.maxNodeExecutions,
    createdAt: timestamp,
    updatedAt: timestamp,
  }
}

async function persistFlowDraft(deps: OrchestrationHandlersDependencies, flow: OrchestrationFlow) {
  const timestamp = deps.now()
  return mutateStore(store => {
    requireChat(store, flow.chatId)
    const normalized: OrchestrationFlow = {
      ...flow,
      name: flow.name.trim(),
      stages: flow.stages,
      maxNodeExecutions: normalizeMaxNodeExecutions(flow.maxNodeExecutions),
      maxRounds: normalizeMaxRounds(flow.maxRounds),
      createdAt: store.orchestrationFlowsById[flow.id]?.createdAt ?? flow.createdAt ?? timestamp,
      updatedAt: timestamp,
    }
    if (!normalized.name) throw new Error('编排流程名称不能为空')
    store.orchestrationFlowsById[normalized.id] = normalized
    const order = store.orchestrationFlowOrderByChatId[normalized.chatId] ?? []
    store.orchestrationFlowOrderByChatId[normalized.chatId] = order.includes(normalized.id) ? order : [...order, normalized.id]
    return normalized
  })
}

function requireFlowPayload(value: unknown): OrchestrationFlow {
  if (!isRecord(value)) throw new Error('编排流程格式无效')
  if (typeof value.id !== 'string' || typeof value.chatId !== 'string' || typeof value.name !== 'string') throw new Error('编排流程格式无效')
  if (!Array.isArray(value.stages)) throw new Error('编排流程节点格式无效')
  return value as unknown as OrchestrationFlow
}

function normalizeMaxRounds(value: number | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_ORCHESTRATION_MAX_ROUNDS
  return Math.min(MAX_ORCHESTRATION_MAX_ROUNDS, Math.max(1, Math.floor(value)))
}

function normalizeMaxNodeExecutions(value: number | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_ORCHESTRATION_MAX_NODE_EXECUTIONS
  return Math.min(MAX_ORCHESTRATION_MAX_NODE_EXECUTIONS, Math.max(1, Math.floor(value)))
}

function readAutoPlanHistory(value: unknown): OrchestrationAutoPlanHistoryEntry[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item): OrchestrationAutoPlanHistoryEntry[] => {
    if (!isRecord(item)) return []
    const id = readOptionalString(item.id)
    const content = readOptionalString(item.content)
    if (!id || !content) return []
    return [{
      id,
      role: item.role === 'assistant' ? 'assistant' : 'user',
      content,
      createdAt: typeof item.createdAt === 'number' && Number.isFinite(item.createdAt) ? item.createdAt : 0,
    }]
  })
}

function appendAutoPlanHistory(
  history: OrchestrationAutoPlanHistoryEntry[],
  instruction: string,
  plan: AutoOrchestrationPlan,
  assistantContent: string,
  timestamp: number,
  newId: (prefix: string) => string,
): OrchestrationAutoPlanHistoryEntry[] {
  const next = [...history]
  const userContent = instruction.trim()
  if (userContent) next.push({ id: newId('auto-history'), role: 'user', content: userContent, createdAt: timestamp })
  next.push({ id: newId('auto-history'), role: 'assistant', content: assistantContent.trim() || summarizeAutoPlanResult(plan), createdAt: timestamp })
  return next
}

function summarizeAutoPlanResult(plan: AutoOrchestrationPlan): string {
  return `已生成「${plan.flowName || '自动编排流程'}」：${plan.nodes.length} 个节点，${plan.roles.length} 个人员，最大节点执行数 ${plan.maxNodeExecutions}。`
}

async function streamAutoPlanCompletion(
  client: ExternalModelClient,
  input: ExternalModelCompletionInput,
  onChunk?: (chunk: string, content: string) => Promise<void> | void,
): Promise<{ content: string }> {
  let content = ''
  const stream = typeof client.stream === 'function' ? client.stream(input) : streamCompleteFallback(client, input)
  for await (const chunk of stream) {
    if (!chunk) continue
    content += chunk
    await onChunk?.(chunk, content)
  }
  if (!content.trim()) throw new Error('外部模型返回格式无效')
  return { content }
}

async function* streamCompleteFallback(client: ExternalModelClient, input: ExternalModelCompletionInput): AsyncIterable<string> {
  const result = await client.complete(input)
  yield result.content
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined
}

function requireString(value: unknown, error: string): string {
  const result = readOptionalString(value)
  if (!result) throw new Error(error)
  return result
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
