import { describe, expect, it } from 'vitest'
import { extractGeminiConversationId, extractSupportedConversationId, getSafeGeminiUrl, getSafeSupportedChatUrl, isSafeGeminiUrl, isSafeSupportedChatUrl, normalizeChatGptGptsUrl, normalizeGrokProjectUrl } from './conversationUrl'
import { buildUnsyncedContext, getContextCursorAfterAck, getUnsyncedMessagesForRole } from './contextSync'
import { parseGroupMentions, roleMentionLabel } from './mentionParser'
import { buildInitPrompt, buildPrompt } from './promptBuilder'
import { createGroupRole, createGroupRolesBatch, createRoleTemplate, deleteGroupRole, deleteRoleTemplate, getAllRoleTemplates, getRoleTemplateById, updateGroupRole, updateRoleTemplate, validateRoleName } from './roleTemplates'
import { createDefaultStore } from './store'
import { DEFAULT_CUSTOM_ROLE_TEMPLATES } from './defaultCustomRoleTemplates'
import type { GroupChat, GroupMessage, GroupRole } from './types'

const defaultCustomTemplateIds = DEFAULT_CUSTOM_ROLE_TEMPLATES.map(template => template.id)

describe('role template utilities', () => {
  it('combines built-in and custom role templates for selection', () => {
    const store = createDefaultStore()
    const custom = createRoleTemplate(store, { name: '观察员', systemPrompt: '观察讨论' }, 'template-custom', 1)

    const templates = getAllRoleTemplates(store)
    const builtin = templates.find(template => template.id === 'builtin-frankl')

    expect(custom).toMatchObject({ type: 'custom' })
    expect(builtin).toMatchObject({
      type: 'builtin',
      name: '弗兰克尔',
      systemPrompt: expect.stringContaining('弗兰克尔式意义顾问'),
    })
    expect(templates[0].type).toBe('builtin')
    expect(templates[templates.length - 1]).toBe(custom)
    expect(getRoleTemplateById(store, 'builtin-frankl')).toBe(builtin)
    expect(getRoleTemplateById(store, custom.id)).toBe(custom)
  })

  it('creates, updates, deletes templates, and creates independent group role instances', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')

    const template = createRoleTemplate(
      store,
      { name: '工程师', description: '关注技术实现', systemPrompt: '从工程角度分析' },
      'template-1',
      1,
    )
    expect(template).toMatchObject({ id: 'template-1', name: '工程师' })

    updateRoleTemplate(store, 'template-1', { name: '架构师', systemPrompt: '关注架构' }, 2)
    const role = createGroupRole(store, { chatId: 'chat-1', templateId: 'template-1' }, 'role-1', 3)
    updateRoleTemplate(store, 'template-1', { name: '技术负责人', systemPrompt: '关注技术决策' }, 4)

    expect(role).toMatchObject({ name: '架构师', systemPrompt: '关注架构', templateId: 'template-1' })
    expect(store.chatsById['chat-1'].roleIds).toEqual(['role-1'])

    expect(() => deleteRoleTemplate(store, 'template-1')).toThrow('该人员库人员已被群聊使用，不能删除')
    expect(store.roleTemplateOrder).toEqual([...defaultCustomTemplateIds, 'template-1'])
    expect(store.rolesById['role-1']).toBe(role)
  })

  it('deletes unused templates', () => {
    const store = createDefaultStore()
    createRoleTemplate(store, { name: '工程师', systemPrompt: '从工程角度分析' }, 'template-1', 1)

    deleteRoleTemplate(store, 'template-1')

    expect(store.roleTemplateOrder).toEqual(defaultCustomTemplateIds)
    expect(store.roleTemplatesById['template-1']).toBeUndefined()
  })

  it('allows empty persona text for templates and temporary people', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')

    const template = createRoleTemplate(store, { name: '观察员', systemPrompt: '' }, 'template-1', 1)
    const libraryRole = createGroupRole(store, { chatId: 'chat-1', templateId: template.id }, 'role-1', 2)
    const temporaryRole = createGroupRole(store, { chatId: 'chat-1', name: '记录员', systemPrompt: '' }, 'role-2', 3)

    expect(template.systemPrompt).toBe('')
    expect(libraryRole.systemPrompt).toBeUndefined()
    expect(temporaryRole.systemPrompt).toBeUndefined()
  })

  it('applies the default chat site to new roles', () => {
    const store = createDefaultStore()
    store.settings.defaultChatSite = 'chatgpt'
    store.chatsById['chat-1'] = makeChat('chat-1')

    const role = createGroupRole(store, { chatId: 'chat-1', name: '工程师', systemPrompt: '从工程角度分析' }, 'role-1', 1)

    expect(role.chatSite).toBe('chatgpt')
  })

  it('uses a role template default site when creating library people', () => {
    const store = createDefaultStore()
    store.settings.defaultChatSite = 'gemini'
    store.chatsById['chat-1'] = makeChat('chat-1')
    const template = createRoleTemplate(store, { name: '研究员', systemPrompt: '关注调研', defaultChatSite: 'claude' }, 'template-1', 1)

    const role = createGroupRole(store, { chatId: 'chat-1', templateId: template.id }, 'role-1', 2)

    expect(template).toMatchObject({ defaultChatSite: 'claude' })
    expect(role.chatSite).toBe('claude')
  })

  it('inherits a ChatGPT GPTs start URL from library people', () => {
    const store = createDefaultStore()
    store.settings.defaultChatSite = 'gemini'
    store.chatsById['chat-1'] = makeChat('chat-1')
    const template = createRoleTemplate(store, {
      name: '飞飞教练',
      systemPrompt: '以教练方式回应',
      defaultChatSite: 'chatgpt',
      chatGptGptsUrl: 'https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian/c/69f7fabe-9878-83a8-a867-88ebb36967d4',
    }, 'template-1', 1)

    const role = createGroupRole(store, { chatId: 'chat-1', templateId: template.id }, 'role-1', 2)

    expect(template.chatGptGptsUrl).toBe('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian')
    expect(role).toMatchObject({
      chatSite: 'chatgpt',
      chatGptGptsUrl: 'https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian',
    })
  })

  it('inherits a Grok project start URL from library people', () => {
    const store = createDefaultStore()
    store.settings.defaultChatSite = 'gemini'
    store.chatsById['chat-1'] = makeChat('chat-1')
    const template = createRoleTemplate(store, {
      name: '项目顾问',
      systemPrompt: '在项目上下文中回应',
      defaultChatSite: 'grok',
      grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81?ref=abc',
    }, 'template-1', 1)

    const role = createGroupRole(store, { chatId: 'chat-1', templateId: template.id }, 'role-1', 2)

    expect(template.grokProjectUrl).toBe('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81')
    expect(role).toMatchObject({
      chatSite: 'grok',
      grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81',
    })
  })

  it('creates group roles in a validated batch without saving temporary people as templates', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    createRoleTemplate(store, { name: '工程师', systemPrompt: '从工程角度分析', defaultChatSite: 'claude' }, 'template-1', 1)

    const roles = createGroupRolesBatch(store, 'chat-1', [
      { source: 'library', roleTemplateId: 'template-1' },
      { source: 'temporary', name: '法务', description: '关注合规', systemPrompt: '从法务角度分析', chatSite: 'gemini' },
    ], () => `role-${store.chatsById['chat-1'].roleIds.length + 1}`, 2)

    expect(roles).toHaveLength(2)
    expect(roles[0]).toMatchObject({ templateId: 'template-1', name: '工程师', systemPrompt: '从工程角度分析', chatSite: 'claude' })
    expect(roles[1]).toMatchObject({ name: '法务', systemPrompt: '从法务角度分析', chatSite: 'gemini' })
    expect(roles[1].templateId).toBeUndefined()
    expect(store.roleTemplateOrder).toEqual([...defaultCustomTemplateIds, 'template-1'])
    expect(Object.keys(store.roleTemplatesById)).toEqual([...defaultCustomTemplateIds, 'template-1'])
  })

  it('creates group roles from built-in templates without storing them as custom templates', () => {
    const store = createDefaultStore()
    store.settings.language = 'zh-CN'
    store.chatsById['chat-1'] = makeChat('chat-1')

    const roles = createGroupRolesBatch(store, 'chat-1', [
      { source: 'library', roleTemplateId: 'builtin-frankl', chatSite: 'claude' },
    ], () => 'role-1', 1)

    expect(roles).toHaveLength(1)
    expect(roles[0]).toMatchObject({
      templateId: 'builtin-frankl',
      name: '弗兰克尔',
      chatSite: 'claude',
      systemPrompt: expect.stringContaining('弗兰克尔式意义顾问'),
    })
    expect(store.roleTemplateOrder).toEqual(defaultCustomTemplateIds)
    expect(store.roleTemplatesById).toEqual(Object.fromEntries(DEFAULT_CUSTOM_ROLE_TEMPLATES.map(template => [template.id, template])))
  })

  it('prevents direct built-in template edits and deletes', () => {
    const store = createDefaultStore()

    expect(() => updateRoleTemplate(store, 'builtin-frankl', { name: '意义顾问', systemPrompt: '改写' }, 1)).toThrow('系统内置人员不能编辑')
    expect(() => deleteRoleTemplate(store, 'builtin-frankl')).toThrow('系统内置人员不能删除')
  })

  it('allows default custom templates to be edited like ordinary custom people', () => {
    const store = createDefaultStore()

    const updated = updateRoleTemplate(store, defaultCustomTemplateIds[0], { name: '产品参谋', systemPrompt: '从产品角度给建议' }, 1)

    expect(updated).toMatchObject({
      id: defaultCustomTemplateIds[0],
      type: 'custom',
      name: '产品参谋',
      systemPrompt: '从产品角度给建议',
    })
  })

  it('allows the same library person on different chat sites in one chat', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    createRoleTemplate(store, { name: '工程师', systemPrompt: '从工程角度分析' }, 'template-1', 1)

    const roles = createGroupRolesBatch(store, 'chat-1', [
      { source: 'library', roleTemplateId: 'template-1', chatSite: 'gemini' },
      { source: 'library', roleTemplateId: 'template-1', chatSite: 'claude' },
    ], () => `role-${store.chatsById['chat-1'].roleIds.length + 1}`, 2)

    expect(roles).toHaveLength(2)
    expect(roles.map(role => ({ templateId: role.templateId, name: role.name, chatSite: role.chatSite }))).toEqual([
      { templateId: 'template-1', name: '工程师', chatSite: 'gemini' },
      { templateId: 'template-1', name: '工程师', chatSite: 'claude' },
    ])
  })

  it('prevents adding the same person to the same chat site twice', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    createRoleTemplate(store, { name: '工程师', systemPrompt: '从工程角度分析' }, 'template-1', 1)
    createGroupRole(store, { chatId: 'chat-1', templateId: 'template-1', chatSite: 'gemini' }, 'role-1', 2)

    expect(() => createGroupRolesBatch(store, 'chat-1', [
      { source: 'library', roleTemplateId: 'template-1', chatSite: 'gemini' },
    ], () => 'role-2', 3)).toThrow('人员已存在：工程师（gemini）')
  })

  it('validates an entire role batch before writing', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    createRoleTemplate(store, { name: '工程师', systemPrompt: '从工程角度分析' }, 'template-1', 1)

    expect(() => createGroupRolesBatch(store, 'chat-1', [
      { source: 'library', roleTemplateId: 'template-1' },
      { source: 'temporary', name: '', systemPrompt: 'invalid' },
    ], () => 'role-new', 2)).toThrow('人员名称不能为空')

    expect(store.chatsById['chat-1'].roleIds).toEqual([])
    expect(store.rolesById).toEqual({})
  })

  it('prevents direct persona edits on existing group people', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    const role = createGroupRole(store, { chatId: 'chat-1', name: '法务', systemPrompt: '从法务角度分析' }, 'role-1', 1)

    expect(() => updateGroupRole(store, 'role-1', { systemPrompt: '修改后的人设' }, 2)).toThrow('群聊内人员人设不可编辑')
    expect(role.systemPrompt).toBe('从法务角度分析')
  })

  it('allows persona edits on people generated by auto orchestration', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    const role = createGroupRole(store, { chatId: 'chat-1', name: '写手', systemPrompt: '旧人设' }, 'role-1', 1)
    role.createdBy = 'orchestration-auto'

    const updated = updateGroupRole(store, 'role-1', { systemPrompt: '新的自动编排人设' }, 2)

    expect(updated.systemPrompt).toBe('新的自动编排人设')
    expect(updated.updatedAt).toBe(2)
  })

  it('allows persona edits on people generated by orchestration templates', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    const role = createGroupRole(store, { chatId: 'chat-1', name: '模板写手', systemPrompt: '旧模板人设' }, 'role-1', 1)
    role.createdBy = 'orchestration-template'

    const updated = updateGroupRole(store, 'role-1', { systemPrompt: '新的模板人设' }, 2)

    expect(updated.systemPrompt).toBe('新的模板人设')
    expect(updated.updatedAt).toBe(2)
  })

  it('resets conversation bindings when a role switches chat site', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = makeChat('chat-1')
    const role = createGroupRole(store, { chatId: 'chat-1', name: '法务', systemPrompt: '从法务角度分析' }, 'role-1', 1)
    role.chatSite = 'gemini'
    role.geminiConversationId = 'conv-1'
    role.geminiConversationUrl = 'https://gemini.google.com/app/conv-1'
    role.lastPromptMessageId = 'msg-1'
    role.lastReplyAt = 10
    role.contextCursor = 3
    role.status = 'ready'

    updateGroupRole(store, 'role-1', { chatSite: 'chatgpt' }, 2)

    expect(role.chatSite).toBe('chatgpt')
    expect(role.geminiConversationId).toBeUndefined()
    expect(role.geminiConversationUrl).toBeUndefined()
    expect(role.lastPromptMessageId).toBeUndefined()
    expect(role.lastReplyAt).toBeUndefined()
    expect(role.contextCursor).toBe(0)
    expect(role.status).toBe('pending')
  })

  it('removes a group person while keeping historical messages', () => {
    const store = createDefaultStore()
    const chat = makeChat('chat-1')
    chat.roleIds = ['role-1']
    chat.messageIds = ['msg-1']
    chat.nextMessageSeq = 2
    store.chatsById[chat.id] = chat
    store.rolesById['role-1'] = makeRole('role-1', '工程师')
    store.messagesById['msg-1'] = {
      ...makeMessage('msg-1', 1, 'assistant', '历史观点'),
      roleId: 'role-1',
      roleName: '工程师',
    }

    deleteGroupRole(store, 'role-1', 2)

    expect(chat.roleIds).toEqual([])
    expect(store.rolesById['role-1']).toBeUndefined()
    expect(chat.messageIds).toEqual(['msg-1'])
    expect(store.messagesById['msg-1']).toMatchObject({
      roleId: 'role-1',
      roleName: '工程师',
      content: '历史观点',
    })
  })

  it('validates role names', () => {
    expect(validateRoleName('')).toBe('人员名称不能为空')
    expect(validateRoleName('研'.repeat(50))).toBeUndefined()
    expect(validateRoleName('研'.repeat(51))).toBe('人员名称不能超过 50 个字')
    expect(validateRoleName('A B')).toBe('人员名称不能包含空白字符')
    expect(validateRoleName('@A')).toBe('人员名称不能包含 @')
    expect(validateRoleName('all')).toBe('人员名称不能是 all')
    expect(validateRoleName('工程师', ['工程师'])).toBe('人员名称已存在：工程师')
    expect(validateRoleName('工程师')).toBeUndefined()
  })
})

describe('mention parser', () => {
  const roles = [makeRole('role-product', '产品'), makeRole('role-pm', '产品经理'), makeRole('role-eng', '工程师')]

  it('can keep no-mention messages as chat records without notifying roles', () => {
    expect(parseGroupMentions('分析这个方案', roles, { defaultTarget: 'none' })).toEqual({
      ok: true,
      content: '分析这个方案',
      targetRoleIds: [],
      mentionedRoleIds: [],
    })
  })

  it('keeps the legacy all-role default when no target option is supplied', () => {
    expect(parseGroupMentions('分析这个方案', roles)).toMatchObject({
      ok: true,
      targetRoleIds: ['role-product', 'role-pm', 'role-eng'],
    })
  })

  it('uses longest-match parsing for overlapping role names', () => {
    expect(parseGroupMentions('@产品经理 看一下', roles)).toEqual({
      ok: true,
      content: '看一下',
      targetRoleIds: ['role-pm'],
      mentionedRoleIds: ['role-pm'],
    })
  })

  it('routes multiple mentions, @all, and @所有人', () => {
    expect(parseGroupMentions('@工程师 @产品经理 评估风险', roles)).toEqual({
      ok: true,
      content: '评估风险',
      targetRoleIds: ['role-eng', 'role-pm'],
      mentionedRoleIds: ['role-eng', 'role-pm'],
    })
    expect(parseGroupMentions('@all @工程师 评估风险', roles)).toMatchObject({
      ok: true,
      content: '评估风险',
      targetRoleIds: ['role-product', 'role-pm', 'role-eng'],
      mentionedRoleIds: ['role-eng'],
      mentionsAll: true,
    })
    expect(parseGroupMentions('@所有人 评估风险', roles, { defaultTarget: 'none' })).toMatchObject({
      ok: true,
      content: '评估风险',
      targetRoleIds: ['role-product', 'role-pm', 'role-eng'],
      mentionedRoleIds: [],
      mentionsAll: true,
    })
  })

  it('routes same-name roles by their site-qualified mention label', () => {
    const sameNameRoles = [
      { ...makeRole('role-gemini', '产品经理'), chatSite: 'gemini' as const },
      { ...makeRole('role-claude', '产品经理'), chatSite: 'claude' as const },
    ]

    expect(parseGroupMentions('@产品经理（Claude） 看一下', sameNameRoles)).toEqual({
      ok: true,
      content: '看一下',
      targetRoleIds: ['role-claude'],
      mentionedRoleIds: ['role-claude'],
    })
  })

  it('routes external model roles by their configured model-qualified mention label', () => {
    const role: GroupRole = {
      ...makeRole('role-api', '弗兰克尔'),
      modelSource: 'external',
      externalModelId: 'model-1',
      chatSite: undefined,
    }
    const options = { externalModelNamesById: { 'model-1': 'OpenRouter Claude' } }

    expect(roleMentionLabel(role, options)).toBe('弗兰克尔（OpenRouter Claude）')
    expect(parseGroupMentions('@弗兰克尔（OpenRouter Claude） 你能做什么', [role], options)).toEqual({
      ok: true,
      content: '你能做什么',
      targetRoleIds: ['role-api'],
      mentionedRoleIds: ['role-api'],
    })
  })

  it('keeps unknown mentions in the message and falls back to all roles', () => {
    expect(parseGroupMentions('@不存在 继续评估', roles)).toEqual({
      ok: true,
      content: '@不存在 继续评估',
      targetRoleIds: ['role-product', 'role-pm', 'role-eng'],
      mentionedRoleIds: [],
    })
  })
})

describe('context sync utilities', () => {
  it('returns unsynced messages in chat order while excluding current user message and own role messages', () => {
    const chat = makeChat('chat-1')
    chat.messageIds = ['msg-1', 'msg-2', 'msg-3', 'msg-4']
    chat.nextMessageSeq = 5
    const role = makeRole('role-a', 'A')
    role.contextCursor = 1
    const userMessage = makeMessage('msg-4', 4, 'user', 'current')
    const messages = [
      makeMessage('msg-1', 1, 'user', 'old'),
      { ...makeMessage('msg-2', 2, 'assistant', 'own'), roleId: 'role-a', roleName: 'A' },
      { ...makeMessage('msg-3', 3, 'assistant', 'other'), roleId: 'role-b', roleName: 'B' },
      userMessage,
    ]

    expect(getUnsyncedMessagesForRole(chat, role, messages, userMessage).map(message => message.id)).toEqual(['msg-3'])
    expect(getContextCursorAfterAck(chat)).toBe(4)
  })

  it('does not sync user messages that were directed to other roles', () => {
    const chat = makeChat('chat-1')
    chat.messageIds = ['msg-1', 'msg-2']
    chat.roleIds = ['role-a', 'role-b']
    const role = makeRole('role-b', 'B')
    const messageForA = { ...makeMessage('msg-1', 1, 'user', 'only for A'), targetRoleIds: ['role-a'] }
    const currentUserMessage = { ...makeMessage('msg-2', 2, 'user', 'current for B'), targetRoleIds: ['role-b'] }

    const context = buildUnsyncedContext(chat, role, [messageForA, currentUserMessage], currentUserMessage)

    expect(context.messages).toEqual([])
    expect(context.contextText).toBe('')
  })

  it('formats truncated unsynced context and reports the latest message cursor', () => {
    const chat = makeChat('chat-1')
    chat.messageIds = ['msg-1', 'msg-2', 'msg-3']
    const role = makeRole('role-a', 'A')
    const currentUserMessage = makeMessage('msg-3', 3, 'user', 'current')
    const messages = [
      makeMessage('msg-1', 1, 'user', 'first user turn'),
      { ...makeMessage('msg-2', 2, 'assistant', 'assistant context'), roleId: 'role-b', roleName: 'B' },
      currentUserMessage,
    ]

    const context = buildUnsyncedContext(chat, role, messages, currentUserMessage, 12)

    expect(context.messages.map(message => message.id)).toEqual(['msg-1', 'msg-2'])
    expect(context.contextText).toBe('tant context')
    expect(context.omittedEarlyContext).toBe(true)
    expect(context.latestSeq).toBe(3)
    expect(getContextCursorAfterAck(chat, messages)).toBe(3)
  })
})

describe('prompt builder', () => {
  it('builds independent prompts with references and without other role context', () => {
    const chat = makeChat('chat-1')
    const role = makeRole('role-a', '工程师')
    role.description = '关注技术风险'
    const userMessage = makeMessage('msg-1', 1, 'user', '你怎么看？')
    userMessage.references = [{ messageId: 'msg-ref', roleName: '产品经理', contentSnapshot: '需要两周上线' }]

    const prompt = buildPrompt({ chat, role, userMessage, roles: [role], language: 'zh-CN' })

    expect(prompt).toContain('你是「工程师」')
    expect(prompt).toContain('用户引用了「产品经理」的观点')
    expect(prompt).toContain('你怎么看？')
    expect(prompt).not.toContain('群聊成员')
  })

  it('can build English internal prompts from the centralized language setting', () => {
    const chat = makeChat('chat-1')
    const role = makeRole('role-a', 'Engineer')
    role.description = 'Focus on technical risks'
    const userMessage = makeMessage('msg-1', 1, 'user', 'What do you think?')

    const prompt = buildPrompt({ chat, role, userMessage, roles: [role], language: 'en' })

    expect(prompt).toContain('You are "Engineer".')
    expect(prompt).toContain('Your responsibility:')
    expect(prompt).toContain('User message:')
    expect(prompt).toContain('Respond in English unless the user explicitly asks for another language.')
    expect(prompt).not.toContain('请以')
  })

  it('skips persona in ordinary prompts when requested', () => {
    const chat = makeChat('chat-1')
    const role = makeRole('role-a', '工程师')
    role.description = '关注技术风险'
    role.systemPrompt = '这是完整人设'
    const userMessage = makeMessage('msg-1', 1, 'user', '你怎么看？')

    const prompt = buildPrompt({ chat, role, userMessage, roles: [role], includePersona: false, language: 'zh-CN' })

    expect(prompt).toContain('你是「工程师」')
    expect(prompt).toContain('你的职责')
    expect(prompt).not.toContain('这是完整人设')
  })

  it('builds collaborative init and message prompts with members and truncated context', () => {
    const chat = { ...makeChat('chat-1'), mode: 'collaborative' as const }
    const roleA = makeRole('role-a', '工程师')
    const roleB = makeRole('role-b', '产品经理')
    roleB.description = '关注用户价值'
    const userMessage = makeMessage('msg-3', 3, 'user', '继续讨论')
    const unsyncedMessages = [{ ...makeMessage('msg-2', 2, 'assistant', '很长的上下文内容'), roleId: 'role-b', roleName: '产品经理' }]

    expect(buildInitPrompt(chat, roleA, [roleA, roleB], true, 'zh-CN')).toContain('群聊成员')

    const prompt = buildPrompt({
      chat,
      role: roleA,
      userMessage,
      roles: [roleA, roleB],
      unsyncedMessages,
      maxContextChars: 4,
      language: 'zh-CN',
    })
    expect(prompt).toContain('群聊成员')
    expect(prompt).toContain('[部分早期上下文已省略]')
    expect(prompt).toContain('继续讨论')
  })
})

describe('Gemini conversation URL utilities', () => {
  it('validates safe Gemini URLs and extracts conversation ids', () => {
    expect(isSafeGeminiUrl('https://gemini.google.com/app/abc123')).toBe(true)
    expect(isSafeGeminiUrl('https://evil.example/app/abc123')).toBe(false)
    expect(isSafeGeminiUrl('http://gemini.google.com/app/abc123')).toBe(false)
    expect(isSafeGeminiUrl('https://gemini.google.com.evil.example/app/abc123')).toBe(false)
    expect(getSafeGeminiUrl('https://evil.example/app/abc123')).toBe('https://gemini.google.com/')
    expect(extractGeminiConversationId('https://gemini.google.com/app/abc123?x=1')).toBe('abc123')
    expect(extractGeminiConversationId('https://gemini.google.com/app/abc123/share')).toBe('abc123')
    expect(extractGeminiConversationId('https://gemini.google.com/app/%E6%B5%8B%E8%AF%95')).toBe('测试')
    expect(extractGeminiConversationId('https://gemini.google.com/')).toBeUndefined()
  })

  it('accepts safe ChatGPT URLs as supported chat conversation URLs', () => {
    expect(isSafeSupportedChatUrl('https://chatgpt.com/c/abc123')).toBe(true)
    expect(isSafeSupportedChatUrl('https://chatgpt.com/g/g-abc-coach/c/abc123')).toBe(true)
    expect(isSafeSupportedChatUrl('https://chat.openai.com/c/abc123')).toBe(true)
    expect(isSafeSupportedChatUrl('https://claude.ai/chat/abc123')).toBe(true)
    expect(isSafeSupportedChatUrl('https://chat.deepseek.com/a/chat/s/abc123')).toBe(true)
    expect(isSafeSupportedChatUrl('https://chatgpt.com.evil.example/c/abc123')).toBe(false)
    expect(isSafeSupportedChatUrl('https://claude.ai.evil.example/chat/abc123')).toBe(false)
    expect(isSafeSupportedChatUrl('https://chat.deepseek.com.evil.example/a/chat/s/abc123')).toBe(false)
    expect(getSafeSupportedChatUrl('https://chatgpt.com/c/abc123')).toBe('https://chatgpt.com/c/abc123')
    expect(getSafeSupportedChatUrl('https://claude.ai/chat/abc123')).toBe('https://claude.ai/chat/abc123')
    expect(getSafeSupportedChatUrl('https://chat.deepseek.com/a/chat/s/abc123')).toBe('https://chat.deepseek.com/a/chat/s/abc123')
    expect(getSafeSupportedChatUrl('https://evil.example/c/abc123')).toBe('https://gemini.google.com/')
    expect(extractSupportedConversationId('https://chatgpt.com/c/%E6%B5%8B%E8%AF%95')).toBe('测试')
    expect(extractSupportedConversationId('https://chatgpt.com/g/g-abc-coach/c/%E6%B5%8B%E8%AF%95')).toBe('测试')
    expect(extractSupportedConversationId('https://claude.ai/chat/%E6%B5%8B%E8%AF%95')).toBe('测试')
    expect(extractSupportedConversationId('https://chat.deepseek.com/a/chat/s/%E6%B5%8B%E8%AF%95')).toBe('测试')
    expect(extractSupportedConversationId('https://chatgpt.com/')).toBeUndefined()
    expect(getSafeSupportedChatUrl('https://evil.example/chat/abc123')).toBe('https://gemini.google.com/')
  })

  it('normalizes ChatGPT GPTs start URLs and rejects unsafe origins', () => {
    expect(normalizeChatGptGptsUrl('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian/c/69f7fabe-9878-83a8-a867-88ebb36967d4')).toBe('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian')
    expect(normalizeChatGptGptsUrl('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian?model=gpt-5')).toBe('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian')
    expect(normalizeChatGptGptsUrl('https://chatgpt.com/g/')).toBeUndefined()
    expect(normalizeChatGptGptsUrl('https://chatgpt.com.evil.example/g/g-LrdzaEiqT-fei-fei-jiao-lian')).toBeUndefined()
  })

  it('normalizes Grok project start URLs and rejects unsafe origins', () => {
    expect(normalizeGrokProjectUrl('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81?source=share')).toBe('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81')
    expect(normalizeGrokProjectUrl('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81/')).toBe('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81')
    expect(normalizeGrokProjectUrl('https://grok.com/project/')).toBeUndefined()
    expect(normalizeGrokProjectUrl('https://grok.com.evil.example/project/a9e415eb-149b-42b8-811a-63b12477ed81')).toBeUndefined()
  })
})

function makeChat(id: string): GroupChat {
  return {
    id,
    name: id,
    mode: 'independent',
    roleIds: [],
    messageIds: [],
    nextMessageSeq: 1,
    status: 'draft',
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeRole(id: string, name: string): GroupRole {
  return {
    id,
    chatId: 'chat-1',
    name,
    status: 'ready',
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeMessage(id: string, seq: number, type: GroupMessage['type'], content: string): GroupMessage {
  return {
    id,
    chatId: 'chat-1',
    seq,
    type,
    content,
    createdAt: 1,
    status: 'received',
  }
}
