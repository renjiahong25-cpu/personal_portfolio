import { describe, expect, it } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupChat, GroupMessage, GroupRole } from '../group/types'
import { formatChatExportMarkdown, safeChatExportFilename } from './chatExport'

describe('team page chat export', () => {
  it('formats a markdown chat record with removed-member historical messages', () => {
    const store = createDefaultStore()
    const chat = makeChat('chat-1')
    chat.name = '产品方案'
    chat.mode = 'collaborative'
    chat.roleIds = ['role-live']
    chat.messageIds = ['msg-user', 'msg-live', 'msg-removed']
    store.chatsById[chat.id] = chat
    store.rolesById['role-live'] = makeRole('role-live', '工程师')
    store.messagesById['msg-user'] = makeMessage('msg-user', 1, 'user', '先评估方案')
    store.messagesById['msg-live'] = {
      ...makeMessage('msg-live', 2, 'assistant', '技术上可行'),
      roleId: 'role-live',
      roleName: '工程师',
    }
    store.messagesById['msg-removed'] = {
      ...makeMessage('msg-removed', 3, 'assistant', '旧观点也保留'),
      roleId: 'role-removed',
      roleName: '旧成员',
    }

    const markdown = formatChatExportMarkdown(store, chat, new Date('2026-05-06T12:34:56+08:00'))

    expect(markdown).toContain('# 产品方案')
    expect(markdown).toContain('- 群聊模式：协作群聊')
    expect(markdown).toContain('- 当前成员：工程师')
    expect(markdown).toContain('## 聊天记录')
    expect(markdown).toContain('### 我')
    expect(markdown).toContain('先评估方案')
    expect(markdown).toContain('### 工程师')
    expect(markdown).toContain('技术上可行')
    expect(markdown).toContain('### 旧成员')
    expect(markdown).toContain('旧观点也保留')
  })

  it('creates a safe markdown export filename', () => {
    expect(safeChatExportFilename('产品/方案:第一版', new Date('2026-05-06T12:34:56'))).toBe('web-agent-产品-方案-第一版-20260506-123456.md')
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
