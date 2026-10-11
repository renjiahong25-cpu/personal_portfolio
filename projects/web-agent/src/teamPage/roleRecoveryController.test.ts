// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupChat, GroupMessage, GroupRole, WebAgentStore } from '../group/types'
import { createTeamPageState } from './appState'
import { createRoleRecoveryController } from './roleRecoveryController'

describe('createRoleRecoveryController', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls the background store while waiting for recovered roles to become ready', async () => {
    const chat = makeChat('chat-1', ['role-1'])
    const role = makeRole(chat.id, 'role-1', '工程师', 'loading')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      rolesById: { [role.id]: role },
    }
    const refreshStore = vi.fn(async () => {
      store.rolesById[role.id] = { ...store.rolesById[role.id], status: 'ready' }
    })
    let settled = false

    createRoleRecoveryController({
      state: createTeamPageState(),
      getStore: () => store,
      getCurrentRoles: () => [store.rolesById[role.id]],
      refreshStore,
      switchChat: vi.fn(),
      renderComposerState: vi.fn(),
      setWindowMinimized: vi.fn(),
      iframeHost: {
        focusRoleFrame: vi.fn(() => false),
        recoverRole: vi.fn(),
      },
      runCommand: vi.fn(async () => undefined),
      showError: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
    })
      .reconnectRolesForSend(chat, [role])
      .then(() => {
        settled = true
      })

    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(1000)

    expect(refreshStore).toHaveBeenCalledWith(false)
    expect(settled).toBe(true)
  })

  it('recovers the role iframe and retries when reply resync finds no ready iframe', async () => {
    const chat = makeChat('chat-1', ['role-1'])
    const role = makeRole(chat.id, 'role-1', '工程师', 'ready')
    const message: GroupMessage = {
      id: 'msg-1',
      chatId: chat.id,
      seq: 1,
      type: 'assistant',
      content: '漏了一半',
      roleId: role.id,
      roleName: role.name,
      createdAt: 1,
      status: 'received',
    }
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      rolesById: { [role.id]: role },
      messagesById: { [message.id]: message },
    }
    const runCommand = vi
      .fn()
      .mockRejectedValueOnce(new Error('人员 iframe 尚未就绪，请先恢复人员'))
      .mockResolvedValue(undefined)
    const refreshStore = vi.fn(async () => {
      store.rolesById[role.id] = { ...store.rolesById[role.id], status: 'ready' }
    })
    const iframeHost = {
      focusRoleFrame: vi.fn(() => false),
      recoverRole: vi.fn(),
    }
    let settled = false

    const log = { info: vi.fn(), warn: vi.fn() }

    createRoleRecoveryController({
      state: createTeamPageState(),
      getStore: () => store,
      getCurrentRoles: () => [store.rolesById[role.id]],
      refreshStore,
      switchChat: vi.fn(),
      renderComposerState: vi.fn(),
      setWindowMinimized: vi.fn(),
      iframeHost,
      runCommand,
      showError: vi.fn(),
      log,
    })
      .resyncMessageReply(message)
      .then(() => {
        settled = true
      })

    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(1000)

    expect(runCommand).toHaveBeenNthCalledWith(1, 'GROUP_MESSAGE_RESYNC_REPLY', { chatId: chat.id, roleId: role.id, messageId: message.id })
    expect(runCommand).toHaveBeenNthCalledWith(2, 'GROUP_ROLE_RECOVER', { chatId: chat.id, roleId: role.id })
    expect(iframeHost.recoverRole).toHaveBeenCalledWith(role)
    expect(runCommand).toHaveBeenNthCalledWith(3, 'GROUP_MESSAGE_RESYNC_REPLY', { chatId: chat.id, roleId: role.id, messageId: message.id })
    expect(log.warn).toHaveBeenCalledWith('message-resync:recover:start', { chatId: chat.id, roleId: role.id, messageId: message.id })
    expect(settled).toBe(true)
  })

  it('recovers the role iframe and retries when reply retry finds no ready iframe', async () => {
    const chat = makeChat('chat-1', ['role-1'])
    const role = makeRole(chat.id, 'role-1', '工程师', 'ready')
    role.lastPromptMessageId = 'msg-timeout'
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      rolesById: { [role.id]: role },
    }
    const runCommand = vi
      .fn()
      .mockRejectedValueOnce(new Error('人员 iframe 尚未就绪，请先恢复人员'))
      .mockResolvedValue(undefined)
    const refreshStore = vi.fn(async () => {
      store.rolesById[role.id] = { ...store.rolesById[role.id], status: 'ready' }
    })
    const iframeHost = {
      focusRoleFrame: vi.fn(() => false),
      recoverRole: vi.fn(),
    }
    const controller = createRoleRecoveryController({
      state: createTeamPageState(),
      getStore: () => store,
      getCurrentRoles: () => [store.rolesById[role.id]],
      refreshStore,
      switchChat: vi.fn(),
      renderComposerState: vi.fn(),
      setWindowMinimized: vi.fn(),
      iframeHost,
      runCommand,
      showError: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
    })

    await controller.retryRoleReply(role, 'msg-timeout')

    expect(runCommand).toHaveBeenNthCalledWith(1, 'GROUP_ROLE_RETRY_REPLY', { chatId: chat.id, roleId: role.id, messageId: 'msg-timeout' })
    expect(runCommand).toHaveBeenNthCalledWith(2, 'GROUP_ROLE_RECOVER', { chatId: chat.id, roleId: role.id })
    expect(iframeHost.recoverRole).toHaveBeenCalledWith(role)
    expect(runCommand).toHaveBeenNthCalledWith(3, 'GROUP_ROLE_RETRY_REPLY', { chatId: chat.id, roleId: role.id, messageId: 'msg-timeout' })
    expect(refreshStore).toHaveBeenCalledWith(false)
  })

  it('manual refresh reloads current site role iframes even when roles are already online', async () => {
    const chat = makeChat('chat-1', ['role-1'])
    const role = makeRole(chat.id, 'role-1', '工程师', 'ready')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      rolesById: { [role.id]: role },
    }
    const refreshStore = vi.fn(async () => {
      store.rolesById[role.id] = { ...store.rolesById[role.id], status: 'ready' }
    })
    const iframeHost = {
      focusRoleFrame: vi.fn(() => false),
      recoverRole: vi.fn(),
    }
    const controller = createRoleRecoveryController({
      state: { ...createTeamPageState(), selectedChatId: chat.id },
      getStore: () => store,
      getCurrentRoles: () => [store.rolesById[role.id]],
      refreshStore,
      switchChat: vi.fn(),
      renderComposerState: vi.fn(),
      setWindowMinimized: vi.fn(),
      iframeHost,
      runCommand: vi.fn(async () => undefined),
      showError: vi.fn(),
      log: { info: vi.fn(), warn: vi.fn() },
    })

    await controller.refreshCurrentChat()

    expect(iframeHost.recoverRole).toHaveBeenCalledWith(role)
  })
})

function makeChat(id: string, roleIds: string[]): GroupChat {
  return {
    id,
    name: '群聊',
    mode: 'independent',
    roleIds,
    messageIds: [],
    nextMessageSeq: 1,
    status: 'running',
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeRole(chatId: string, id: string, name: string, status: GroupRole['status']): GroupRole {
  return {
    id,
    chatId,
    name,
    status,
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}
