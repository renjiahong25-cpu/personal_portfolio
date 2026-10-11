import { describe, expect, it } from 'vitest'
import { createDefaultStore } from './store'
import type { GroupChat, GroupMessage, GroupRole } from './types'
import { buildExternalModelPrompt } from './externalModelContext'

describe('external model context', () => {
  it('uses only the target role memory in independent mode while preserving explicit references', () => {
    const store = createDefaultStore()
    const chat = makeChat('independent')
    const engineer = makeRole('role-engineer', '工程师')
    const designer = makeRole('role-designer', '设计师')
    const messages = [
      makeUserMessage('msg-1', 1, '工程师先看架构', ['role-engineer']),
      makeAssistantMessage('msg-2', 2, engineer, '工程师自己的旧观点'),
      makeAssistantMessage('msg-3', 3, designer, '设计师不该自动进入专家上下文'),
      makeUserMessage('msg-4', 4, '继续分析，并回应引用', ['role-engineer'], {
        messageId: 'msg-3',
        roleId: 'role-designer',
        roleName: '设计师',
        contentSnapshot: '设计师这条被用户明确引用',
      }),
    ]
    install(store, chat, [engineer, designer], messages)

    const prompt = buildExternalModelPrompt(store, chat, engineer, messages[3], [engineer, designer])

    expect(prompt.content).toContain('工程师自己的旧观点')
    expect(prompt.content).toContain('设计师这条被用户明确引用')
    expect(prompt.content).not.toContain('设计师不该自动进入专家上下文')
  })

  it('uses the full group timeline in collaborative mode', () => {
    const store = createDefaultStore()
    const chat = makeChat('collaborative')
    const engineer = makeRole('role-engineer', '工程师')
    const designer = makeRole('role-designer', '设计师')
    const messages = [
      makeUserMessage('msg-1', 1, '大家先看方案', ['role-engineer', 'role-designer']),
      makeAssistantMessage('msg-2', 2, designer, '设计师的群聊观点'),
      makeUserMessage('msg-3', 3, '工程师继续', ['role-engineer']),
    ]
    install(store, chat, [engineer, designer], messages)

    const prompt = buildExternalModelPrompt(store, chat, engineer, messages[2], [engineer, designer])

    expect(prompt.content).toContain('设计师的群聊观点')
    expect(prompt.content).toContain('大家先看方案')
    expect(prompt.content).toContain('工程师继续')
  })

  it('compresses older context when the prompt exceeds the context budget', () => {
    const store = createDefaultStore()
    store.settings.language = 'zh-CN'
    store.settings.maxContextChars = 360
    const chat = makeChat('collaborative')
    const engineer = makeRole('role-engineer', '工程师')
    const messages = [
      makeUserMessage('msg-1', 1, '早期需求 '.repeat(30), ['role-engineer']),
      makeAssistantMessage('msg-2', 2, engineer, '早期回复 '.repeat(30)),
      makeUserMessage('msg-3', 3, '最新问题', ['role-engineer']),
    ]
    install(store, chat, [engineer], messages)

    const prompt = buildExternalModelPrompt(store, chat, engineer, messages[2], [engineer])

    expect(prompt.memoryPatch).toMatchObject({
      scope: 'chat',
      id: chat.id,
      summarizedThroughSeq: 2,
    })
    expect(prompt.content).toContain('历史摘要')
    expect(prompt.content).toContain('最新问题')
  })
})

function install(store: ReturnType<typeof createDefaultStore>, chat: GroupChat, roles: GroupRole[], messages: GroupMessage[]): void {
  store.chatsById[chat.id] = chat
  store.chatOrder = [chat.id]
  for (const role of roles) store.rolesById[role.id] = role
  for (const message of messages) store.messagesById[message.id] = message
  chat.roleIds = roles.map(role => role.id)
  chat.messageIds = messages.map(message => message.id)
  chat.nextMessageSeq = messages.length + 1
}

function makeChat(mode: GroupChat['mode']): GroupChat {
  return {
    id: 'chat-1',
    name: '方案讨论',
    mode,
    roleIds: [],
    messageIds: [],
    nextMessageSeq: 1,
    status: 'ready',
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeRole(id: string, name: string): GroupRole {
  return {
    id,
    chatId: 'chat-1',
    name,
    modelSource: 'external',
    externalModelId: 'model-1',
    status: 'ready',
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeUserMessage(id: string, seq: number, content: string, targetRoleIds: string[], reference?: NonNullable<GroupMessage['references']>[number]): GroupMessage {
  return {
    id,
    chatId: 'chat-1',
    seq,
    type: 'user',
    content,
    targetRoleIds,
    references: reference ? [reference] : undefined,
    createdAt: seq,
    status: 'received',
  }
}

function makeAssistantMessage(id: string, seq: number, role: GroupRole, content: string): GroupMessage {
  return {
    id,
    chatId: 'chat-1',
    seq,
    type: 'assistant',
    content,
    roleId: role.id,
    roleName: role.name,
    createdAt: seq,
    status: 'received',
  }
}
