import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupMessage, GroupRole, WebAgentStore, RuntimeFrameBinding } from '../group/types'
import { createControlActionExecutor, readTaskResult } from './controlHandlers'
import type { RuntimeMessage } from './runtimeClient'

describe('background control handlers', () => {
  it('routes create-and-post through existing chat, role, and message runtime commands', async () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true
    const routeRuntimeMessage = vi.fn(async (message: RuntimeMessage) => {
      if (message.type === 'GROUP_CHAT_CREATE') {
        const chat = {
          id: 'chat-1',
          name: String(message.name),
          mode: 'independent',
          roleIds: [],
          messageIds: [],
          nextMessageSeq: 1,
          status: 'draft',
          createdAt: 1,
          updatedAt: 1,
        } satisfies WebAgentStore['chatsById'][string]
        store.chatsById[chat.id] = chat
        store.chatOrder = [chat.id]
        store.currentChatId = chat.id
        return { ok: true, chat, store }
      }
      if (message.type === 'GROUP_ROLES_CREATE_BATCH') {
        const roles: GroupRole[] = [
          makeRole('chat-1', 'role-eng', '工程师'),
          makeRole('chat-1', 'role-pm', '产品经理'),
        ]
        store.chatsById['chat-1'].roleIds = roles.map(role => role.id)
        store.rolesById = Object.fromEntries(roles.map(role => [role.id, role]))
        return { ok: true, roles, store }
      }
      if (message.type === 'GROUP_CHAT_SWITCH') {
        store.currentChatId = String(message.chatId)
        return { ok: true, store }
      }
      if (message.type === 'GROUP_MESSAGE_SEND') {
        const userMessage: GroupMessage = {
          id: 'msg-task',
          chatId: 'chat-1',
          seq: 1,
          type: 'user',
          content: '请评估这个方案',
          targetRoleIds: ['role-eng', 'role-pm'],
          mentionsAll: true,
          createdAt: 2,
          status: 'pending',
          deliveryStatus: { 'role-eng': 'pending', 'role-pm': 'pending' },
        }
        store.messagesById[userMessage.id] = userMessage
        store.chatsById['chat-1'].messageIds = [userMessage.id]
        return { ok: true, message: userMessage, store }
      }
      throw new Error(`unexpected route ${message.type}`)
    })
    const executor = createControlActionExecutor({
      loadStore: async () => store,
      routeRuntimeMessage,
      runtimeFrames: { list: () => [], getByRole: () => undefined },
      webAgentPage: vi.fn(),
      openDoubaoPage: vi.fn(),
      openDeepSeekPage: vi.fn(),
      focusSiteTab: vi.fn(async () => ({ created: true, tabId: 1 })),
      ensureSiteFrame: vi.fn(async () => undefined),
      waitFor: async () => undefined,
      now: () => 10,
    })

    const result = await executor({
      id: 'cmd-1',
      action: 'run.createAndPost',
      payload: {
        chat: { name: '方案评审', mode: 'independent' },
        roles: [
          { source: 'temporary', name: '工程师', systemPrompt: '从工程角度评估' },
          { source: 'temporary', name: '产品经理', systemPrompt: '从产品角度评估' },
        ],
        task: { target: 'all', content: '请评估这个方案' },
        options: { waitForReady: false, waitForReplies: false },
      },
    })

    expect(result.ok).toBe(true)
    expect(result.data).toMatchObject({
      chat: { id: 'chat-1', name: '方案评审' },
      roles: [{ id: 'role-eng' }, { id: 'role-pm' }],
      taskMessage: { id: 'msg-task', status: 'pending' },
    })
    expect(routeRuntimeMessage).toHaveBeenNthCalledWith(1, expect.objectContaining({
      type: 'GROUP_CHAT_CREATE',
      name: '方案评审',
      mode: 'independent',
    }))
    expect(routeRuntimeMessage).toHaveBeenNthCalledWith(2, expect.objectContaining({
      type: 'GROUP_ROLES_CREATE_BATCH',
      chatId: 'chat-1',
      items: expect.arrayContaining([
        expect.objectContaining({ source: 'temporary', name: '工程师', systemPrompt: '从工程角度评估' }),
      ]),
    }))
    expect(routeRuntimeMessage).toHaveBeenNthCalledWith(3, expect.objectContaining({
      type: 'GROUP_CHAT_SWITCH',
      chatId: 'chat-1',
    }))
    expect(routeRuntimeMessage).toHaveBeenNthCalledWith(4, expect.objectContaining({
      type: 'GROUP_MESSAGE_SEND',
      chatId: 'chat-1',
      raw: '@所有人 请评估这个方案',
    }))
  })

  it('fails run.createAndPost when role bindings never become ready', async () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true
    const routeRuntimeMessage = vi.fn(async (message: RuntimeMessage) => {
      if (message.type === 'GROUP_CHAT_CREATE') {
        const chat = {
          id: 'chat-2',
          name: String(message.name),
          mode: 'independent',
          roleIds: [],
          messageIds: [],
          nextMessageSeq: 1,
          status: 'draft',
          createdAt: 1,
          updatedAt: 1,
        } satisfies WebAgentStore['chatsById'][string]
        store.chatsById[chat.id] = chat
        store.chatOrder = [chat.id]
        return { ok: true, chat, store }
      }
      if (message.type === 'GROUP_ROLES_CREATE_BATCH') {
        const roles: GroupRole[] = [makeRole('chat-2', 'role-eng', '工程师')]
        store.chatsById['chat-2'].roleIds = roles.map(role => role.id)
        store.rolesById = Object.fromEntries(roles.map(role => [role.id, role]))
        return { ok: true, roles, store }
      }
      if (message.type === 'GROUP_CHAT_SWITCH') {
        store.currentChatId = String(message.chatId)
        return { ok: true, store }
      }
      throw new Error(`unexpected route ${message.type}`)
    })
    let fakeNow = 10
    const ensureSiteFrame = vi.fn(async () => undefined)
    const executor = createControlActionExecutor({
      loadStore: async () => store,
      routeRuntimeMessage,
      runtimeFrames: { list: () => [], getByRole: () => undefined },
      webAgentPage: vi.fn(),
      openDoubaoPage: vi.fn(),
      openDeepSeekPage: vi.fn(),
      focusSiteTab: vi.fn(async () => ({ created: true, tabId: 1 })),
      ensureSiteFrame,
      waitFor: async (ms: number) => {
        fakeNow += ms
      },
      now: () => fakeNow,
    })

    const result = await executor({
      id: 'cmd-2',
      action: 'run.createAndPost',
      payload: {
        chat: { name: '方案评审', mode: 'independent' },
        roles: [{ source: 'temporary', name: '工程师', systemPrompt: '从工程角度评估' }],
        task: { target: 'all', content: '请评估这个方案' },
        options: { timeoutMs: 1000 },
      },
    })

    expect(result.ok).toBe(false)
    expect(result.error).toMatchObject({
      code: 'internal_error',
      message: expect.stringContaining('角色绑定初始化超时'),
    })
    expect(ensureSiteFrame).toHaveBeenCalledWith({ chatId: 'chat-2', roleId: 'role-eng', chatSite: undefined })
    expect(routeRuntimeMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'GROUP_MESSAGE_SEND' }))
  })

  it('opens a site frame for unbound roles and posts once the frame reports ready', async () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true
    const bindings: RuntimeFrameBinding[] = []
    const ensureSiteFrame = vi.fn(async (input: { chatId: string; roleId: string }) => {
      bindings.push({ chatId: input.chatId, roleId: input.roleId, tabId: 7, frameId: 0, ready: true, lastSeenAt: 10 })
    })
    const routeRuntimeMessage = vi.fn(async (message: RuntimeMessage) => {
      if (message.type === 'GROUP_CHAT_CREATE') {
        const chat = {
          id: 'chat-3',
          name: String(message.name),
          mode: 'independent',
          roleIds: [],
          messageIds: [],
          nextMessageSeq: 1,
          status: 'draft',
          createdAt: 1,
          updatedAt: 1,
        } satisfies WebAgentStore['chatsById'][string]
        store.chatsById[chat.id] = chat
        store.chatOrder = [chat.id]
        return { ok: true, chat, store }
      }
      if (message.type === 'GROUP_ROLES_CREATE_BATCH') {
        const roles: GroupRole[] = [makeRole('chat-3', 'role-ds', 'DEEPSEEK')]
        store.chatsById['chat-3'].roleIds = roles.map(role => role.id)
        store.rolesById = Object.fromEntries(roles.map(role => [role.id, role]))
        return { ok: true, roles, store }
      }
      if (message.type === 'GROUP_CHAT_SWITCH') {
        store.currentChatId = String(message.chatId)
        return { ok: true, store }
      }
      if (message.type === 'GROUP_MESSAGE_SEND') {
        const userMessage: GroupMessage = {
          id: 'msg-gw',
          chatId: 'chat-3',
          seq: 1,
          type: 'user',
          content: '1+1=?',
          targetRoleIds: ['role-ds'],
          mentionsAll: true,
          createdAt: 2,
          status: 'pending',
          deliveryStatus: { 'role-ds': 'pending' },
        }
        store.messagesById[userMessage.id] = userMessage
        store.chatsById['chat-3'].messageIds = [userMessage.id]
        return { ok: true, message: userMessage, store }
      }
      throw new Error(`unexpected route ${message.type}`)
    })
    let fakeNow = 10
    const executor = createControlActionExecutor({
      loadStore: async () => store,
      routeRuntimeMessage,
      runtimeFrames: {
        list: () => bindings,
        getByRole: (chatId: string, roleId: string) => bindings.find(binding => binding.chatId === chatId && binding.roleId === roleId),
      },
      webAgentPage: vi.fn(),
      openDoubaoPage: vi.fn(),
      openDeepSeekPage: vi.fn(),
      focusSiteTab: vi.fn(async () => ({ created: true, tabId: 7 })),
      ensureSiteFrame,
      waitFor: async (ms: number) => {
        fakeNow += ms
      },
      now: () => fakeNow,
    })

    const result = await executor({
      id: 'cmd-3',
      action: 'run.createAndPost',
      payload: {
        chat: { name: '模型网关-DEEPSEEK', mode: 'independent', reuse: { strategy: 'by-name' } },
        roles: [{ name: 'DEEPSEEK', chatSite: 'deepseek', modelSource: 'site' }],
        task: { target: 'all', content: '1+1=?' },
        options: { waitForReady: true, waitForReplies: false, activateChat: false, webAgentPage: false, timeoutMs: 10_000 },
      },
    })

    expect(result.ok).toBe(true)
    expect(ensureSiteFrame).toHaveBeenCalledWith({ chatId: 'chat-3', roleId: 'role-ds', chatSite: undefined })
    expect(routeRuntimeMessage).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'GROUP_MESSAGE_SEND' }))
  })

  it('stops a task for pending/sent roles and thinking roles without stopping later turns', async () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true
    store.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '评审群',
      mode: 'independent',
      roleIds: ['role-eng', 'role-pm'],
      messageIds: ['msg-task'],
      nextMessageSeq: 2,
      status: 'ready',
      createdAt: 1,
      updatedAt: 2,
    }
    store.rolesById['role-eng'] = {
      ...makeRole('chat-1', 'role-eng', '工程师'),
      status: 'thinking',
      lastPromptMessageId: 'msg-task',
      replyAttemptId: 'ra-1',
    }
    store.rolesById['role-pm'] = makeRole('chat-1', 'role-pm', '产品经理')
    store.messagesById['msg-task'] = {
      id: 'msg-task',
      chatId: 'chat-1',
      seq: 1,
      type: 'user',
      content: '请评估这个方案',
      targetRoleIds: ['role-eng', 'role-pm'],
      createdAt: 1,
      status: 'sent',
      deliveryStatus: { 'role-eng': 'sent', 'role-pm': 'pending' },
    }
    const routeRuntimeMessage = vi.fn(async (message: RuntimeMessage) => {
      if (message.type === 'GROUP_ROLE_STOP_REPLY' && typeof message.roleId === 'string') {
        const role = store.rolesById[message.roleId]
        if (role) role.status = 'stopped'
        return { ok: true, store }
      }
      throw new Error(`unexpected route ${message.type}`)
    })
    const loadStore = vi.fn(async () => store)
    const executor = createControlActionExecutor({
      loadStore,
      routeRuntimeMessage,
      runtimeFrames: { list: () => [], getByRole: () => undefined },
      webAgentPage: vi.fn(),
      openDoubaoPage: vi.fn(),
      openDeepSeekPage: vi.fn(),
      focusSiteTab: vi.fn(async () => ({ created: true, tabId: 1 })),
      ensureSiteFrame: vi.fn(async () => undefined),
      waitFor: async () => undefined,
      now: () => 10,
    })

    const result = await executor({
      id: 'cmd-stop-1',
      action: 'task.stop',
      payload: { chatId: 'chat-1', messageId: 'msg-task' },
    })

    expect(result.ok).toBe(true)
    expect(result.data).toMatchObject({
      chatId: 'chat-1',
      messageId: 'msg-task',
      targetRoleIds: ['role-eng', 'role-pm'],
      stoppedRoleIds: ['role-eng', 'role-pm'],
      failedRoleIds: [],
      roles: [{ id: 'role-eng', status: 'stopped' }, { id: 'role-pm', status: 'stopped' }],
    })
    expect(routeRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'GROUP_ROLE_STOP_REPLY',
      chatId: 'chat-1',
      roleId: 'role-eng',
    }))
    expect(routeRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'GROUP_ROLE_STOP_REPLY',
      chatId: 'chat-1',
      roleId: 'role-pm',
    }))
    expect(loadStore).toHaveBeenCalled()
  })

  it('falls back to thinking roles when no messageId is given and reports failed stops', async () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true
    store.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '评审群',
      mode: 'independent',
      roleIds: ['role-eng'],
      messageIds: [],
      nextMessageSeq: 1,
      status: 'ready',
      createdAt: 1,
      updatedAt: 2,
    }
    store.rolesById['role-eng'] = {
      ...makeRole('chat-1', 'role-eng', '工程师'),
      status: 'thinking',
      lastPromptMessageId: 'msg-task',
      replyAttemptId: 'ra-1',
    }
    const routeRuntimeMessage = vi.fn(async (message: RuntimeMessage) => {
      if (message.type === 'GROUP_ROLE_STOP_REPLY' && typeof message.roleId === 'string' && store.rolesById[message.roleId]) {
        const role = store.rolesById[message.roleId]
        role.status = 'stopped'
        return { ok: true, store }
      }
      throw new Error(`unexpected route ${message.type}`)
    })
    const executor = createControlActionExecutor({
      loadStore: async () => store,
      routeRuntimeMessage,
      runtimeFrames: { list: () => [], getByRole: () => undefined },
      webAgentPage: vi.fn(),
      openDoubaoPage: vi.fn(),
      openDeepSeekPage: vi.fn(),
      focusSiteTab: vi.fn(async () => ({ created: true, tabId: 1 })),
      ensureSiteFrame: vi.fn(async () => undefined),
      waitFor: async () => undefined,
      now: () => 10,
    })

    const result = await executor({
      id: 'cmd-stop-2',
      action: 'task.stop',
      payload: { chatId: 'chat-1', roleIds: ['role-eng', 'role-missing'] },
    })

    expect(result.ok).toBe(true)
    expect(result.data).toMatchObject({
      chatId: 'chat-1',
      messageId: null,
      targetRoleIds: ['role-eng', 'role-missing'],
      stoppedRoleIds: ['role-eng'],
      failedRoleIds: ['role-missing'],
    })
    expect(routeRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'GROUP_ROLE_STOP_REPLY',
      roleId: 'role-eng',
    }))
    expect(routeRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'GROUP_ROLE_STOP_REPLY',
      roleId: 'role-missing',
    }))
  })

  it('reads replies belonging to a task message without leaking later turns', () => {
    const store = createDefaultStore()
    store.chatsById['chat-1'] = {
      id: 'chat-1',
      name: '评审群',
      mode: 'independent',
      roleIds: ['role-eng', 'role-pm'],
      messageIds: ['msg-task', 'msg-eng', 'msg-pm', 'msg-next', 'msg-later'],
      nextMessageSeq: 6,
      status: 'ready',
      createdAt: 1,
      updatedAt: 5,
    }
    store.rolesById['role-eng'] = makeRole('chat-1', 'role-eng', '工程师')
    store.rolesById['role-pm'] = makeRole('chat-1', 'role-pm', '产品经理')
    store.messagesById['msg-task'] = {
      id: 'msg-task',
      chatId: 'chat-1',
      seq: 1,
      type: 'user',
      content: '请评估这个方案',
      targetRoleIds: ['role-eng', 'role-pm'],
      createdAt: 1,
      status: 'received',
      deliveryStatus: { 'role-eng': 'received', 'role-pm': 'received' },
    }
    store.messagesById['msg-eng'] = makeReply('msg-eng', 'role-eng', '工程师', '工程可行', 2, 'msg-task')
    store.messagesById['msg-pm'] = makeReply('msg-pm', 'role-pm', '产品经理', '产品可行', 3, 'msg-task')
    store.messagesById['msg-next'] = {
      id: 'msg-next',
      chatId: 'chat-1',
      seq: 4,
      type: 'user',
      content: '继续追问',
      targetRoleIds: ['role-eng'],
      createdAt: 4,
      status: 'pending',
    }
    store.messagesById['msg-later'] = makeReply('msg-later', 'role-eng', '工程师', '后续回复', 5, 'msg-next')

    expect(readTaskResult(store, 'chat-1', 'msg-task')).toMatchObject({
      messageId: 'msg-task',
      status: 'received',
      replies: [
        { messageId: 'msg-eng', roleId: 'role-eng', content: '工程可行' },
        { messageId: 'msg-pm', roleId: 'role-pm', content: '产品可行' },
      ],
    })
  })
})

function makeRole(chatId: string, id: string, name: string): GroupRole {
  return {
    id,
    chatId,
    name,
    systemPrompt: `${name}人设`,
    status: 'ready',
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeReply(id: string, roleId: string, roleName: string, content: string, seq: number, sourceMessageId: string): GroupMessage {
  return {
    id,
    chatId: 'chat-1',
    seq,
    type: 'assistant',
    content,
    roleId,
    roleName,
    sourceMessageId,
    createdAt: seq,
    status: 'received',
  }
}
