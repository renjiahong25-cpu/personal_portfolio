import { WEB_AGENT_CONTROL_CAPABILITIES, controlFailure, controlSuccess, isRecord, type ControlHttpCommand, type ControlHttpResult } from '../shared/localControlProtocol'
import type { ChatSite, GroupChat, GroupMessage, GroupRole, WebAgentStore, RoomMode, RuntimeFrameBinding } from '../group/types'
import type { RuntimeMessage } from './runtimeClient'

export interface SiteFrameAssignmentInput {
  chatId: string
  roleId: string
  chatSite?: ChatSite
}

export interface ControlActionExecutorDependencies {
  loadStore(): Promise<WebAgentStore>
  routeRuntimeMessage(message: RuntimeMessage): Promise<unknown>
  runtimeFrames: {
    list(): RuntimeFrameBinding[]
    getByRole(chatId: string, roleId: string): RuntimeFrameBinding | undefined
  }
  webAgentPage(): Promise<void> | void
  openDoubaoPage(): Promise<void>
  openDeepSeekPage(): Promise<void>
  focusSiteTab(urlPrefix: string, fallbackUrl: string): Promise<{ tabId?: number; created: boolean }>
  ensureSiteFrame(input: SiteFrameAssignmentInput): Promise<void>
  waitFor(ms: number): Promise<void>
  now(): number
}

export interface TaskReadResult {
  chatId: string
  messageId: string
  status: GroupMessage['status']
  targetRoleIds: string[]
  pendingRoleIds: string[]
  receivedRoleIds: string[]
  errorRoleIds: string[]
  replies: Array<{
    messageId: string
    roleId: string
    roleName: string
    content: string
    status: GroupMessage['status']
    conversationUrl?: string
  }>
}

type RuntimeResponse = { ok?: boolean; error?: string; [key: string]: unknown }

const DEFAULT_WAIT_TIMEOUT_MS = 300_000
const WAIT_POLL_INTERVAL_MS = 500

export function createControlActionExecutor(deps: ControlActionExecutorDependencies): (command: ControlHttpCommand) => Promise<ControlHttpResult> {
  return async command => {
    try {
      const store = await deps.loadStore()
      if (!store.settings.agentControlEnabled) {
        return controlFailure(command.id, 'control_disabled', 'Web Agent 尚未启用本机智能体控制。', {
          hint: '请在 Web Agent 页面设置里开启“本机智能体控制”。',
          recoverable: true,
        })
      }

      const data = await executeEnabledCommand(command, store, deps)
      return controlSuccess(command.id, data)
    } catch (error) {
      return controlFailure(command.id, errorCode(error), error instanceof Error ? error.message : String(error), { recoverable: true })
    }
  }
}

async function executeEnabledCommand(command: ControlHttpCommand, initialStore: WebAgentStore, deps: ControlActionExecutorDependencies): Promise<unknown> {
  switch (command.action) {
    case 'store.get':
      return { store: initialStore, bindings: deps.runtimeFrames.list(), capabilities: WEB_AGENT_CONTROL_CAPABILITIES }
    case 'chat.list':
      return { chats: initialStore.chatOrder.map(chatId => chatSummary(initialStore.chatsById[chatId], initialStore)).filter(Boolean) }
    case 'chat.get':
      return chatDetails(initialStore, requirePayloadString(command.payload, 'chatId'))
    case 'chat.create':
      return createChat(command.payload, deps)
    case 'chat.activate':
      return activateChat(command.payload, deps)
    case 'chat.initialize':
      return initializeChat(command.payload, deps)
    case 'roles.batchAdd':
      return batchAddRoles(command.payload, deps)
    case 'task.post':
      return postTask(command.payload, initialStore, deps)
    case 'task.read':
      return readTaskResult(initialStore, requirePayloadString(command.payload, 'chatId'), requirePayloadString(command.payload, 'messageId'))
    case 'task.stop':
      return stopTask(command.payload, initialStore, deps)
    case 'task.wait':
      return waitForTask(command.payload, deps)
    case 'run.createAndPost':
      return createAndPost(command.payload, initialStore, deps)
    default:
      throw new Error(`不支持的控制命令：${command.action}`)
  }
}

async function createChat(payload: unknown, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少群聊参数')
  const response = await routeOk(deps, {
    type: 'GROUP_CHAT_CREATE',
    name: readString(input.name) ?? '新群聊',
    description: readString(input.description),
    mode: readRoomMode(input.mode),
  })
  return {
    chat: response.chat,
    store: response.store,
  }
}

async function activateChat(payload: unknown, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const chatId = requirePayloadString(payload, 'chatId')
  const response = await routeOk(deps, { type: 'GROUP_CHAT_SWITCH', chatId })
  await deps.webAgentPage()
  return { chatId, store: response.store }
}

async function initializeChat(payload: unknown, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少初始化参数')
  const chatId = requireString(input.chatId, '缺少群聊 ID')
  const waitForReady = input.waitForReady !== false
  const timeoutMs = readPositiveNumber(input.timeoutMs) ?? 120_000
  await activateChat({ chatId }, deps)
  if (!waitForReady) return { chatId, readyRoleIds: [], pendingRoleIds: [], failedRoleIds: [], timedOut: false }
  return waitForReadyRoles(chatId, timeoutMs, deps)
}

async function batchAddRoles(payload: unknown, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少人员参数')
  return routeOk(deps, {
    type: 'GROUP_ROLES_CREATE_BATCH',
    chatId: requireString(input.chatId, '缺少群聊 ID'),
    items: Array.isArray(input.items) ? input.items : [],
  })
}

async function postTask(payload: unknown, store: WebAgentStore, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少任务参数')
  const chatId = requireString(input.chatId, '缺少群聊 ID')
  const content = requireString(input.content, '任务内容不能为空')
  const target = input.target ?? 'all'
  const response = await routeOk(deps, {
    type: 'GROUP_MESSAGE_SEND',
    chatId,
    raw: buildTaskRaw(store, chatId, target, content),
    reference: input.reference,
    image: input.image,
  })
  return {
    message: response.message,
    deliveries: response.deliveries,
  }
}

async function waitForTask(payload: unknown, deps: ControlActionExecutorDependencies): Promise<TaskReadResult & { timedOut: boolean }> {
  const input = requireRecord(payload, '缺少等待任务参数')
  const chatId = requireString(input.chatId, '缺少群聊 ID')
  const messageId = requireString(input.messageId, '缺少消息 ID')
  const timeoutMs = readPositiveNumber(input.timeoutMs) ?? DEFAULT_WAIT_TIMEOUT_MS
  const start = deps.now()

  while (deps.now() - start <= timeoutMs) {
    const store = await deps.loadStore()
    const result = readTaskResult(store, chatId, messageId)
    if (result.pendingRoleIds.length === 0) return { ...result, timedOut: false }
    await deps.waitFor(WAIT_POLL_INTERVAL_MS)
  }

  return { ...readTaskResult(await deps.loadStore(), chatId, messageId), timedOut: true }
}

async function stopTask(payload: unknown, store: WebAgentStore, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少停止任务参数')
  const chatId = requireString(input.chatId, '缺少群聊 ID')
  const messageId = readString(input.messageId)
  const explicitRoleIds = Array.isArray(input.roleIds) ? input.roleIds.map(roleId => String(roleId)) : []

  const chat = store.chatsById[chatId]
  if (!chat) throw new Error(`找不到群聊：${chatId}`)

  const targetSet = new Set(explicitRoleIds)
  if (messageId) {
    const message = store.messagesById[messageId]
    if (!message || message.chatId !== chatId) throw new Error(`找不到任务消息：${messageId}`)
    for (const [roleId, status] of Object.entries(message.deliveryStatus ?? {})) {
      if (status === 'pending' || status === 'sent') targetSet.add(roleId)
    }
  }
  if (targetSet.size === 0) {
    for (const roleId of chat.roleIds) {
      if (store.rolesById[roleId]?.status === 'thinking') targetSet.add(roleId)
    }
  }

  const stoppedRoleIds: string[] = []
  const failedRoleIds: string[] = []
  for (const roleId of targetSet) {
    try {
      await routeOk(deps, { type: 'GROUP_ROLE_STOP_REPLY', chatId, roleId })
      stoppedRoleIds.push(roleId)
    } catch {
      failedRoleIds.push(roleId)
    }
  }

  const latestStore = await deps.loadStore()
  return {
    chatId,
    messageId: messageId ?? null,
    targetRoleIds: [...targetSet],
    stoppedRoleIds,
    failedRoleIds,
    roles: stoppedRoleIds
      .map(roleId => latestStore.rolesById[roleId])
      .filter((role): role is GroupRole => Boolean(role))
      .map(roleSummary),
  }
}

async function createAndPost(payload: unknown, initialStore: WebAgentStore, deps: ControlActionExecutorDependencies): Promise<unknown> {
  const input = requireRecord(payload, '缺少一键运行参数')
  const chatInput = requireRecord(input.chat, '缺少群聊参数')
  const taskInput = requireRecord(input.task, '缺少任务参数')
  const options = isRecord(input.options) ? input.options : {}
  const taskImage = isRecord(taskInput.image) ? {
    data: requireString(taskInput.image.data, '缺少图片数据'),
    mimeType: requireString(taskInput.image.mimeType, '缺少图片 MIME 类型'),
  } : undefined
  const chat = await resolveRunChat(chatInput, initialStore, deps)
  const rolesInput = Array.isArray(input.roles) ? input.roles : []

  let roles: GroupRole[] = chat.roleIds.map(roleId => initialStore.rolesById[roleId]).filter((role): role is GroupRole => Boolean(role))
  if (rolesInput.length > 0) {
    const roleResponse = await routeOk(deps, { type: 'GROUP_ROLES_CREATE_BATCH', chatId: chat.id, items: rolesInput })
    roles = Array.isArray(roleResponse.roles) ? roleResponse.roles as GroupRole[] : roles
  }

  if (options.activateChat !== false) {
    await routeOk(deps, { type: 'GROUP_CHAT_SWITCH', chatId: chat.id })
  }
  const isNewChat = !isRecord(chatInput.reuse)
  if (isNewChat) {
    await deps.openDoubaoPage()
  } else if (options.webAgentPage !== false) {
    await deps.webAgentPage()
  }

  if (options.waitForReady !== false) {
    const timeoutMs = readPositiveNumber(options.timeoutMs) ?? 120_000
    const readyResult = await waitForReadyRoles(chat.id, timeoutMs, deps)
    if (readyResult.timedOut && readyResult.readyRoleIds.length === 0) {
      throw new Error('角色绑定初始化超时，请确认目标 AI 页面已正确加载')
    }
  }

  const latestStore = await deps.loadStore()
  const postResponse = await postTask({
    chatId: chat.id,
    target: taskInput.target ?? 'all',
    content: requireString(taskInput.content, '任务内容不能为空'),
    reference: taskInput.reference,
    image: taskImage,
  }, latestStore, deps) as { message?: GroupMessage }
  const message = requireMessage(postResponse.message)
  const waitForReplies = options.waitForReplies === true
  const readResult = waitForReplies
    ? await waitForTask({ chatId: chat.id, messageId: message.id, timeoutMs: readPositiveNumber(options.timeoutMs) }, deps)
    : readTaskResult(await deps.loadStore(), chat.id, message.id)

  return {
    chat: chatSummary(chat, await deps.loadStore()),
    roles: roles.map(roleSummary),
    taskMessage: {
      id: message.id,
      content: message.content,
      status: message.status,
    },
    replies: readResult.replies,
    warnings: 'timedOut' in readResult && readResult.timedOut ? ['任务等待超时，返回部分回复。'] : undefined,
  }
}

async function resolveRunChat(chatInput: Record<string, unknown>, store: WebAgentStore, deps: ControlActionExecutorDependencies): Promise<GroupChat> {
  const reuse = isRecord(chatInput.reuse) ? chatInput.reuse : undefined
  const strategy = readString(reuse?.strategy) ?? 'none'
  if (strategy === 'by-id') {
    const chat = store.chatsById[requireString(reuse?.chatId, '缺少复用群聊 ID')]
    if (!chat) throw new Error('找不到要复用的群聊')
    return chat
  }
  if (strategy === 'by-name') {
    const name = requireString(chatInput.name, '群聊名称不能为空')
    const chat = store.chatOrder.map(chatId => store.chatsById[chatId]).find(item => item?.name === name)
    if (chat) return chat
  }

  const response = await routeOk(deps, {
    type: 'GROUP_CHAT_CREATE',
    name: readString(chatInput.name) ?? '智能体任务群聊',
    description: readString(chatInput.description),
    mode: readRoomMode(chatInput.mode),
  })
  return requireChat(response.chat)
}

export function readTaskResult(store: WebAgentStore, chatId: string, messageId: string): TaskReadResult {
  const chat = store.chatsById[chatId]
  if (!chat) throw new Error(`找不到群聊：${chatId}`)
  const taskMessage = store.messagesById[messageId]
  if (!taskMessage || taskMessage.chatId !== chat.id || taskMessage.type !== 'user') throw new Error(`找不到任务消息：${messageId}`)

  const targetRoleIds = taskMessage.targetRoleIds?.length ? taskMessage.targetRoleIds : [...chat.roleIds]
  const explicitReplies = chat.messageIds
    .map(id => store.messagesById[id])
    .filter((message): message is GroupMessage => Boolean(message && message.type === 'assistant' && message.sourceMessageId === taskMessage.id && Boolean(message.roleId)))
  const replies = explicitReplies.length > 0 ? explicitReplies : inferRepliesAfterTask(store, chat, taskMessage, targetRoleIds)
  const statusByRole = taskMessage.deliveryStatus ?? {}

  return {
    chatId: chat.id,
    messageId: taskMessage.id,
    status: taskMessage.status,
    targetRoleIds,
    pendingRoleIds: targetRoleIds.filter(roleId => statusByRole[roleId] === 'pending' || statusByRole[roleId] === 'sent'),
    receivedRoleIds: targetRoleIds.filter(roleId => statusByRole[roleId] === 'received'),
    errorRoleIds: targetRoleIds.filter(roleId => statusByRole[roleId] === 'error'),
    replies: replies.map(reply => {
      const role = reply.roleId ? store.rolesById[reply.roleId] : undefined
      return {
        messageId: reply.id,
        roleId: reply.roleId ?? '',
        roleName: reply.roleName ?? role?.name ?? '未知人员',
        content: reply.content,
        status: reply.status,
        conversationUrl: role?.geminiConversationUrl,
      }
    }),
  }
}

function inferRepliesAfterTask(store: WebAgentStore, chat: GroupChat, taskMessage: GroupMessage, targetRoleIds: string[]): GroupMessage[] {
  const taskIndex = chat.messageIds.indexOf(taskMessage.id)
  if (taskIndex < 0) return []
  const targetSet = new Set(targetRoleIds)
  const replies: GroupMessage[] = []
  for (let index = taskIndex + 1; index < chat.messageIds.length; index += 1) {
    const message = store.messagesById[chat.messageIds[index]]
    if (!message) continue
    if (message.type === 'user') break
    if (message.type === 'assistant' && message.roleId && targetSet.has(message.roleId)) replies.push(message)
  }
  return replies
}

function buildTaskRaw(store: WebAgentStore, chatId: string, target: unknown, content: string): string {
  if (target === 'all') return `@所有人 ${content}`
  if (isRecord(target) && Array.isArray(target.roleNames)) return `${target.roleNames.map(name => `@${String(name)}`).join(' ')} ${content}`
  if (isRecord(target) && Array.isArray(target.roleIds)) {
    const names = target.roleIds
      .map(roleId => store.rolesById[String(roleId)])
      .filter((role): role is GroupRole => Boolean(role && role.chatId === chatId))
      .map(role => `@${role.name}`)
    if (names.length === 0) throw new Error('没有找到可发送的目标人员')
    return `${names.join(' ')} ${content}`
  }
  throw new Error('不支持的任务目标')
}

async function waitForReadyRoles(chatId: string, timeoutMs: number, deps: ControlActionExecutorDependencies): Promise<{
  chatId: string
  readyRoleIds: string[]
  pendingRoleIds: string[]
  failedRoleIds: string[]
  timedOut: boolean
}> {
  const start = deps.now()
  while (deps.now() - start <= timeoutMs) {
    const store = await deps.loadStore()
    const chat = store.chatsById[chatId]
    if (!chat) throw new Error(`找不到群聊：${chatId}`)
    const roles = chat.roleIds.map(roleId => store.rolesById[roleId]).filter((role): role is GroupRole => Boolean(role))
    const siteRoles = roles.filter(role => role.modelSource !== 'external')
    const bindings = deps.runtimeFrames.list()
    const readyRoleIds = siteRoles.filter(role => bindings.some(binding => binding.chatId === chat.id && binding.roleId === role.id && binding.ready)).map(role => role.id)
    const failedRoleIds = siteRoles.filter(role => role.status === 'error').map(role => role.id)
    const pendingRoleIds = siteRoles.map(role => role.id).filter(roleId => !readyRoleIds.includes(roleId) && !failedRoleIds.includes(roleId))
    if (pendingRoleIds.length === 0) return { chatId, readyRoleIds, pendingRoleIds, failedRoleIds, timedOut: false }
    const unboundSiteRoles = siteRoles.filter(role => pendingRoleIds.includes(role.id) && !deps.runtimeFrames.getByRole(chat.id, role.id))
    for (const role of unboundSiteRoles) {
      try {
        await deps.ensureSiteFrame({ chatId: chat.id, roleId: role.id, chatSite: role.chatSite })
      } catch {
        // 下一轮轮询会重试；broker 内部自带节流
      }
    }
    await deps.waitFor(WAIT_POLL_INTERVAL_MS)
  }
  const store = await deps.loadStore()
  const chat = store.chatsById[chatId]
  const bindings = deps.runtimeFrames.list()
  const roleIds = chat?.roleIds ?? []
  const readyRoleIds = roleIds.filter(roleId => bindings.some(binding => binding.chatId === chatId && binding.roleId === roleId && binding.ready))
  return {
    chatId,
    readyRoleIds,
    pendingRoleIds: roleIds.filter(roleId => !readyRoleIds.includes(roleId)),
    failedRoleIds: roleIds.filter(roleId => store.rolesById[roleId]?.status === 'error'),
    timedOut: true,
  }
}

async function routeOk(deps: ControlActionExecutorDependencies, message: RuntimeMessage): Promise<RuntimeResponse> {
  const response = await deps.routeRuntimeMessage(message) as RuntimeResponse
  if (response?.ok === false) throw new Error(response.error || `${message.type} 执行失败`)
  return response
}

function chatSummary(chat: GroupChat | undefined, store: WebAgentStore): unknown {
  if (!chat) return undefined
  return {
    id: chat.id,
    name: chat.name,
    description: chat.description,
    mode: chat.mode,
    status: chat.status,
    roleCount: chat.roleIds.length,
    messageCount: chat.messageIds.length,
    current: store.currentChatId === chat.id,
    createdAt: chat.createdAt,
    updatedAt: chat.updatedAt,
  }
}

function chatDetails(store: WebAgentStore, chatId: string): unknown {
  const chat = store.chatsById[chatId]
  if (!chat) throw new Error(`找不到群聊：${chatId}`)
  return {
    chat: chatSummary(chat, store),
    roles: chat.roleIds.map(roleId => store.rolesById[roleId]).filter((role): role is GroupRole => Boolean(role)).map(roleSummary),
    messages: chat.messageIds.map(messageId => store.messagesById[messageId]).filter(Boolean),
  }
}

function roleSummary(role: GroupRole): unknown {
  return {
    id: role.id,
    name: role.name,
    description: role.description,
    chatSite: role.chatSite,
    modelSource: role.modelSource,
    status: role.status,
    conversationUrl: role.geminiConversationUrl,
  }
}

function requirePayloadString(payload: unknown, key: string): string {
  return requireString(requireRecord(payload, '缺少命令参数')[key], `缺少 ${key}`)
}

function requireRecord(value: unknown, message: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(message)
  return value
}

function requireString(value: unknown, message: string): string {
  const text = readString(value)
  if (!text) throw new Error(message)
  return text
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined
}

function readPositiveNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined
}

function readRoomMode(value: unknown): RoomMode | undefined {
  return value === 'collaborative' || value === 'independent' ? value : undefined
}

function requireChat(value: unknown): GroupChat {
  if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.roleIds)) throw new Error('群聊创建返回格式无效')
  return value as unknown as GroupChat
}

function requireMessage(value: unknown): GroupMessage {
  if (!isRecord(value) || typeof value.id !== 'string' || value.type !== 'user') throw new Error('任务消息返回格式无效')
  return value as unknown as GroupMessage
}

function errorCode(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  if (message.includes('找不到群聊')) return 'chat_not_found'
  if (message.includes('找不到人员')) return 'role_not_found'
  if (message.includes('不支持')) return 'invalid_request'
  return 'internal_error'
}
