import { normalizeChatGptGptsUrl, normalizeGrokProjectUrl } from './conversationUrl'
import { BUILTIN_ROLE_TEMPLATES, getBuiltinRoleTemplate, isBuiltinRoleTemplateId } from './builtinRoleTemplates'
import type { ChatSite, GroupChat, GroupRole, WebAgentStore, RoleModelSource, RoleTemplate } from './types'
import { localizeRoleTemplate } from '../shared/i18n'

export const ROLE_NAME_MAX_CHARACTERS = 50

export interface RoleTemplateInput {
  name: string
  description?: string
  systemPrompt?: string
  defaultModelSource?: RoleModelSource
  defaultChatSite?: ChatSite
  defaultExternalModelId?: string
  chatGptGptsUrl?: string
  grokProjectUrl?: string
}

export interface GroupRoleInput {
  chatId: string
  createdBy?: GroupRole['createdBy']
  templateId?: string
  modelSource?: RoleModelSource
  chatSite?: ChatSite
  externalModelId?: string
  name?: string
  description?: string
  systemPrompt?: string
  avatarColor?: string
  chatGptGptsUrl?: string
  grokProjectUrl?: string
}

export type GroupRoleBatchInput =
  | {
      source: 'library'
      roleTemplateId: string
      modelSource?: RoleModelSource
      chatSite?: ChatSite
      externalModelId?: string
      avatarColor?: string
    }
  | {
      source: 'temporary'
      createdBy?: GroupRole['createdBy']
      name: string
      description?: string
      systemPrompt: string
      modelSource?: RoleModelSource
      chatSite?: ChatSite
      externalModelId?: string
      avatarColor?: string
    }

type PreparedGroupRoleBatchItem = Omit<GroupRoleInput, 'chatId'> & {
  name: string
  systemPrompt?: string
}

interface GraphemeSegment {
  segment: string
}

interface GraphemeSegmenter {
  segment(value: string): Iterable<GraphemeSegment>
}

interface IntlWithSegmenter {
  Segmenter?: new (locale: string | undefined, options: { granularity: 'grapheme' }) => GraphemeSegmenter
}

export function validateRoleName(name: string, existingNames: string[] = []): string | undefined {
  const trimmed = name.trim()
  if (!trimmed) return '人员名称不能为空'
  if (countUserPerceivedCharacters(trimmed) > ROLE_NAME_MAX_CHARACTERS) return `人员名称不能超过 ${ROLE_NAME_MAX_CHARACTERS} 个字`
  if (/\s/.test(trimmed)) return '人员名称不能包含空白字符'
  if (trimmed.includes('@')) return '人员名称不能包含 @'
  if (trimmed.toLowerCase() === 'all') return '人员名称不能是 all'
  if (existingNames.some(existingName => existingName.toLowerCase() === trimmed.toLowerCase())) return `人员名称已存在：${trimmed}`
  return undefined
}

export function assertValidRoleName(name: string, existingRoles: GroupRole[], currentRoleId?: string): string {
  const trimmed = name.trim()
  const existingNames = existingRoles
    .filter(role => role.id !== currentRoleId)
    .map(role => role.name)
  const error = validateRoleName(trimmed, existingNames)
  if (error) throw new Error(error)
  return trimmed
}

export function createRoleTemplate(
  store: WebAgentStore,
  input: RoleTemplateInput,
  id: string,
  now: number,
): RoleTemplate {
  const name = assertValidRoleName(input.name, [])
  const systemPrompt = assertValidSystemPrompt(input.systemPrompt)
  const defaultModelSource = input.defaultModelSource === 'external' ? 'external' : 'site'
  const defaultChatSite = defaultModelSource === 'site' ? input.defaultChatSite ?? store.settings.defaultChatSite : undefined
  const template: RoleTemplate = {
    id,
    type: 'custom',
    name,
    defaultModelSource,
    systemPrompt,
    createdAt: now,
    updatedAt: now,
  }
  if (defaultChatSite) template.defaultChatSite = defaultChatSite
  if (defaultModelSource === 'external') template.defaultExternalModelId = requireExternalModelId(store, input.defaultExternalModelId)
  const chatGptGptsUrl = defaultChatSite === 'chatgpt' ? normalizeOptionalChatGptGptsUrl(input.chatGptGptsUrl) : undefined
  if (chatGptGptsUrl) template.chatGptGptsUrl = chatGptGptsUrl
  const grokProjectUrl = defaultChatSite === 'grok' ? normalizeOptionalGrokProjectUrl(input.grokProjectUrl) : undefined
  if (grokProjectUrl) template.grokProjectUrl = grokProjectUrl

  const description = input.description?.trim()
  if (description) template.description = description

  store.roleTemplatesById[id] = template
  if (!store.roleTemplateOrder.includes(id)) store.roleTemplateOrder.push(id)
  return template
}

export function getCustomRoleTemplates(store: WebAgentStore): RoleTemplate[] {
  return store.roleTemplateOrder
    .map(templateId => store.roleTemplatesById[templateId])
    .filter((template): template is RoleTemplate => Boolean(template) && template.type !== 'builtin')
}

export function getAllRoleTemplates(store: WebAgentStore): RoleTemplate[] {
  return [...BUILTIN_ROLE_TEMPLATES, ...getCustomRoleTemplates(store)]
}

export function getRoleTemplateById(store: WebAgentStore, templateId: string): RoleTemplate | undefined {
  return getBuiltinRoleTemplate(templateId) ?? store.roleTemplatesById[templateId]
}

export function updateRoleTemplate(
  store: WebAgentStore,
  templateId: string,
  patch: RoleTemplateInput,
  now: number,
): RoleTemplate {
  if (isBuiltinRoleTemplateId(templateId)) throw new Error('系统内置人员不能编辑')
  const template = store.roleTemplatesById[templateId]
  if (!template) throw new Error(`找不到人员库人员：${templateId}`)

  template.name = assertValidRoleName(patch.name, [])
  template.defaultModelSource = patch.defaultModelSource === 'external' ? 'external' : 'site'
  if (template.defaultModelSource === 'external') {
    template.defaultExternalModelId = requireExternalModelId(store, patch.defaultExternalModelId)
    delete template.defaultChatSite
  } else {
    delete template.defaultExternalModelId
    template.defaultChatSite = patch.defaultChatSite ?? template.defaultChatSite ?? store.settings.defaultChatSite
  }
  template.systemPrompt = assertValidSystemPrompt(patch.systemPrompt)
  template.updatedAt = now
  const chatGptGptsUrl = template.defaultModelSource !== 'external' && template.defaultChatSite === 'chatgpt' ? normalizeOptionalChatGptGptsUrl(patch.chatGptGptsUrl) : undefined
  if (chatGptGptsUrl) {
    template.chatGptGptsUrl = chatGptGptsUrl
  } else {
    delete template.chatGptGptsUrl
  }
  const grokProjectUrl = template.defaultModelSource !== 'external' && template.defaultChatSite === 'grok' ? normalizeOptionalGrokProjectUrl(patch.grokProjectUrl) : undefined
  if (grokProjectUrl) {
    template.grokProjectUrl = grokProjectUrl
  } else {
    delete template.grokProjectUrl
  }

  const description = patch.description?.trim()
  if (description) {
    template.description = description
  } else {
    delete template.description
  }

  return template
}

export function deleteRoleTemplate(store: WebAgentStore, templateId: string): void {
  if (isBuiltinRoleTemplateId(templateId)) throw new Error('系统内置人员不能删除')
  const usage = getRoleTemplateUsage(store, templateId)
  if (usage.usedByChatIds.length > 0) {
    throw new Error('该人员库人员已被群聊使用，不能删除')
  }

  delete store.roleTemplatesById[templateId]
  store.roleTemplateOrder = store.roleTemplateOrder.filter(id => id !== templateId)
}

export function getRoleTemplateUsage(store: WebAgentStore, templateId: string): { usedByRoleIds: string[]; usedByChatIds: string[] } {
  const usedByRoleIds: string[] = []
  const usedByChatIds = new Set<string>()

  for (const role of Object.values(store.rolesById)) {
    if (role.templateId !== templateId) continue
    usedByRoleIds.push(role.id)
    usedByChatIds.add(role.chatId)
  }

  return { usedByRoleIds, usedByChatIds: [...usedByChatIds] }
}

export function createGroupRole(
  store: WebAgentStore,
  input: GroupRoleInput,
  id: string,
  now: number,
): GroupRole {
  const chat = store.chatsById[input.chatId]
  if (!chat) throw new Error(`找不到群聊：${input.chatId}`)

  const template = input.templateId ? getLocalizedRoleTemplateById(store, input.templateId) : undefined
  if (input.templateId && !template) throw new Error(`找不到人员库人员：${input.templateId}`)

  const name = assertValidRoleName(input.name ?? template?.name ?? '', [])
  const modelSource = input.modelSource ?? template?.defaultModelSource ?? 'site'
  const externalModelId = modelSource === 'external' ? requireExternalModelId(store, input.externalModelId ?? template?.defaultExternalModelId) : undefined
  const chatSite = modelSource === 'external' ? undefined : input.chatSite ?? template?.defaultChatSite ?? store.settings.defaultChatSite
  assertUniqueRoleModelIdentity(name, modelSource, chatSite, externalModelId, getChatRoles(store, chat), store.settings.defaultChatSite)
  const role: GroupRole = {
    id,
    chatId: input.chatId,
    modelSource,
    name,
    status: modelSource === 'external' ? 'ready' : 'pending',
    contextCursor: 0,
    createdAt: now,
    updatedAt: now,
  }
  if (chatSite) role.chatSite = chatSite
  if (externalModelId) role.externalModelId = externalModelId

  if (input.templateId) role.templateId = input.templateId
  if (input.createdBy) role.createdBy = input.createdBy

  const chatGptGptsUrl = modelSource !== 'external' && chatSite === 'chatgpt' ? normalizeOptionalChatGptGptsUrl(input.chatGptGptsUrl ?? template?.chatGptGptsUrl) : undefined
  if (chatGptGptsUrl) role.chatGptGptsUrl = chatGptGptsUrl
  const grokProjectUrl = modelSource !== 'external' && chatSite === 'grok' ? normalizeOptionalGrokProjectUrl(input.grokProjectUrl ?? template?.grokProjectUrl) : undefined
  if (grokProjectUrl) role.grokProjectUrl = grokProjectUrl

  const description = (input.description ?? template?.description)?.trim()
  if (description) role.description = description

  const systemPrompt = (input.systemPrompt ?? template?.systemPrompt)?.trim()
  if (systemPrompt) role.systemPrompt = systemPrompt

  const avatarColor = input.avatarColor?.trim()
  if (avatarColor) role.avatarColor = avatarColor

  store.rolesById[id] = role
  chat.roleIds.push(id)
  chat.updatedAt = now
  return role
}

export function updateGroupRole(
  store: WebAgentStore,
  roleId: string,
  patch: Partial<GroupRoleInput>,
  now: number,
): GroupRole {
  const role = store.rolesById[roleId]
  if (!role) throw new Error(`找不到人员：${roleId}`)

  const chat = store.chatsById[role.chatId]
  if (!chat) throw new Error(`找不到群聊：${role.chatId}`)

  const nextName = patch.name !== undefined ? assertValidRoleName(patch.name, []) : role.name
  const nextModelSource = patch.modelSource ?? role.modelSource ?? 'site'
  const nextExternalModelId = nextModelSource === 'external' ? requireExternalModelId(store, patch.externalModelId ?? role.externalModelId) : undefined
  const nextChatSite = nextModelSource === 'external' ? undefined : patch.chatSite ?? role.chatSite ?? store.settings.defaultChatSite
  if (patch.name !== undefined || patch.modelSource !== undefined || patch.externalModelId !== undefined || patch.chatSite !== undefined) {
    assertUniqueRoleModelIdentity(nextName, nextModelSource, nextChatSite, nextExternalModelId, getChatRoles(store, chat), store.settings.defaultChatSite, roleId)
  }
  if (patch.name !== undefined) role.name = nextName
  if (patch.description !== undefined) {
    const description = patch.description.trim()
    if (description) {
      role.description = description
    } else {
      delete role.description
    }
  }
  if (patch.systemPrompt !== undefined) {
    if (role.createdBy !== 'orchestration-auto' && role.createdBy !== 'orchestration-template') throw new Error('群聊内人员人设不可编辑')
    const systemPrompt = assertValidSystemPrompt(patch.systemPrompt)
    if (systemPrompt) {
      role.systemPrompt = systemPrompt
    } else {
      delete role.systemPrompt
    }
  }
  if (patch.modelSource !== undefined || patch.externalModelId !== undefined || patch.chatSite !== undefined) {
    role.modelSource = nextModelSource
    if (nextChatSite) {
      role.chatSite = nextChatSite
    } else {
      delete role.chatSite
    }
    if (nextExternalModelId) {
      role.externalModelId = nextExternalModelId
    } else {
      delete role.externalModelId
    }
    role.contextCursor = 0
    role.status = 'pending'
    delete role.geminiConversationId
    delete role.geminiConversationUrl
    if (nextChatSite !== 'chatgpt') delete role.chatGptGptsUrl
    if (nextChatSite !== 'grok') delete role.grokProjectUrl
    delete role.lastPromptMessageId
    delete role.lastReplyAt
  }
  if (patch.chatGptGptsUrl !== undefined) {
    const chatGptGptsUrl = normalizeOptionalChatGptGptsUrl(patch.chatGptGptsUrl)
    if (nextModelSource !== 'external' && nextChatSite === 'chatgpt' && chatGptGptsUrl) {
      role.chatGptGptsUrl = chatGptGptsUrl
    } else {
      delete role.chatGptGptsUrl
    }
  }
  if (patch.grokProjectUrl !== undefined) {
    const grokProjectUrl = normalizeOptionalGrokProjectUrl(patch.grokProjectUrl)
    if (nextModelSource !== 'external' && nextChatSite === 'grok' && grokProjectUrl) {
      role.grokProjectUrl = grokProjectUrl
    } else {
      delete role.grokProjectUrl
    }
  }
  if (patch.avatarColor !== undefined) {
    const avatarColor = patch.avatarColor.trim()
    if (avatarColor) {
      role.avatarColor = avatarColor
    } else {
      delete role.avatarColor
    }
  }

  role.updatedAt = now
  chat.updatedAt = now
  return role
}

export function deleteGroupRole(store: WebAgentStore, roleId: string, now: number): void {
  const role = store.rolesById[roleId]
  if (!role) return

  const chat = store.chatsById[role.chatId]
  if (chat) {
    chat.roleIds = chat.roleIds.filter(id => id !== roleId)
    chat.updatedAt = now
  }
  delete store.rolesById[roleId]
}

export function createGroupRolesBatch(
  store: WebAgentStore,
  chatId: string,
  items: GroupRoleBatchInput[],
  idFactory: () => string,
  now: number,
): GroupRole[] {
  if (items.length === 0) throw new Error('添加人员列表不能为空')

  const chat = store.chatsById[chatId]
  if (!chat) throw new Error(`找不到群聊：${chatId}`)

  const prepared = items.map((item, index) => prepareBatchItem(store, item, index))
  const identities = new Set(getChatRoles(store, chat).map(role => roleModelIdentityKey(role.name, role.modelSource ?? 'site', role.chatSite ?? store.settings.defaultChatSite, role.externalModelId)))
  for (const item of prepared) {
    const modelSource = item.modelSource ?? 'site'
    const identityKey = roleModelIdentityKey(item.name, modelSource, item.chatSite ?? store.settings.defaultChatSite, item.externalModelId)
    if (identities.has(identityKey)) throw new Error(duplicateRoleModelMessage(item.name, modelSource, item.chatSite ?? store.settings.defaultChatSite, item.externalModelId))
    identities.add(identityKey)
  }

  return prepared.map(item => createGroupRole(store, { chatId, ...item }, idFactory(), now))
}

function prepareBatchItem(store: WebAgentStore, item: GroupRoleBatchInput, index: number): PreparedGroupRoleBatchItem {
  if (item.source === 'library') {
    const template = getRoleTemplateById(store, item.roleTemplateId)
    if (!template) throw new Error(`找不到人员库人员：${item.roleTemplateId}`)
    const localizedTemplate = localizeRoleTemplate(template, store.settings.language)
    return {
      templateId: item.roleTemplateId,
      modelSource: item.modelSource,
      chatSite: item.chatSite ?? localizedTemplate.defaultChatSite ?? store.settings.defaultChatSite,
      externalModelId: item.externalModelId ?? localizedTemplate.defaultExternalModelId,
      name: assertValidRoleName(localizedTemplate.name, []),
      description: localizedTemplate.description,
      systemPrompt: normalizeSystemPrompt(localizedTemplate.systemPrompt),
      avatarColor: item.avatarColor,
      chatGptGptsUrl: localizedTemplate.chatGptGptsUrl,
      grokProjectUrl: localizedTemplate.grokProjectUrl,
    }
  }

  if (item.source === 'temporary') {
    return {
      modelSource: item.modelSource,
      createdBy: item.createdBy,
      chatSite: item.chatSite ?? store.settings.defaultChatSite,
      externalModelId: item.externalModelId,
      name: assertValidRoleName(item.name, []),
      description: item.description,
      systemPrompt: normalizeSystemPrompt(item.systemPrompt),
      avatarColor: item.avatarColor,
    }
  }

  throw new Error(`第 ${index + 1} 个添加项无效`)
}

function getLocalizedRoleTemplateById(store: WebAgentStore, templateId: string): RoleTemplate | undefined {
  const template = getRoleTemplateById(store, templateId)
  return template ? localizeRoleTemplate(template, store.settings.language) : undefined
}

function assertUniqueRoleModelIdentity(
  name: string,
  modelSource: RoleModelSource,
  chatSite: ChatSite | undefined,
  externalModelId: string | undefined,
  existingRoles: GroupRole[],
  defaultChatSite: ChatSite,
  currentRoleId?: string,
): void {
  const identityKey = roleModelIdentityKey(name, modelSource, chatSite ?? defaultChatSite, externalModelId)
  const duplicate = existingRoles.some(role => (
    role.id !== currentRoleId &&
    roleModelIdentityKey(role.name, role.modelSource ?? 'site', role.chatSite ?? defaultChatSite, role.externalModelId) === identityKey
  ))
  if (duplicate) throw new Error(duplicateRoleModelMessage(name, modelSource, chatSite ?? defaultChatSite, externalModelId))
}

function roleModelIdentityKey(name: string, modelSource: RoleModelSource, chatSite: ChatSite, externalModelId: string | undefined): string {
  return modelSource === 'external'
    ? `${name.trim().toLowerCase()}:external:${externalModelId ?? ''}`
    : `${name.trim().toLowerCase()}:site:${chatSite}`
}

function duplicateRoleModelMessage(name: string, modelSource: RoleModelSource, chatSite: ChatSite, externalModelId: string | undefined): string {
  return modelSource === 'external'
    ? `人员已存在：${name}（外部模型 ${externalModelId ?? ''}）`
    : `人员已存在：${name}（${chatSite}）`
}

function requireExternalModelId(store: WebAgentStore, value: string | undefined): string {
  const externalModelId = value?.trim()
  if (!externalModelId || !store.settings.externalModelsById[externalModelId]) throw new Error('请选择有效的外部模型')
  return externalModelId
}

function assertValidSystemPrompt(systemPrompt: string | undefined): string {
  return normalizeSystemPrompt(systemPrompt)
}

function normalizeSystemPrompt(systemPrompt: string | undefined): string {
  const trimmed = systemPrompt?.trim() ?? ''
  return trimmed
}

function normalizeOptionalChatGptGptsUrl(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  if (!trimmed) return undefined
  const normalized = normalizeChatGptGptsUrl(trimmed)
  if (!normalized) throw new Error('GPTs 链接必须是 chatgpt.com/g/... 格式')
  return normalized
}

function normalizeOptionalGrokProjectUrl(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  if (!trimmed) return undefined
  const normalized = normalizeGrokProjectUrl(trimmed)
  if (!normalized) throw new Error('Grok 项目链接必须是 grok.com/project/... 格式')
  return normalized
}

function countUserPerceivedCharacters(value: string): number {
  const Segmenter = (Intl as IntlWithSegmenter).Segmenter
  if (!Segmenter) return [...value].length
  return [...new Segmenter(undefined, { granularity: 'grapheme' }).segment(value)].length
}

function getChatRoles(store: WebAgentStore, chat: GroupChat): GroupRole[] {
  return chat.roleIds
    .map(roleId => store.rolesById[roleId])
    .filter((role): role is GroupRole => Boolean(role))
}
