// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import type { GroupChat, GroupMessage, GroupRole, RoomMode } from '../group/types'
import { createTeamPageState } from './appState'
import { createChatHeaderView } from './chatHeaderView'

describe('chat header view', () => {
  it('only shows orchestration for collaborative chats', () => {
    const harness = createHarness('collaborative')

    harness.view.renderChatHeader()

    expect(harness.openOrchestrationEl.hidden).toBe(false)

    harness.chat.mode = 'independent'
    harness.view.renderChatHeader()

    expect(harness.openOrchestrationEl.hidden).toBe(true)
  })

  it('renders chat status and member counts in English mode', () => {
    const harness = createHarness('collaborative', 'en')
    harness.roles.push({ id: 'role-1', chatId: harness.chat.id, name: 'Engineer', status: 'ready', contextCursor: 0, createdAt: 1, updatedAt: 1 })
    harness.messages.push({ id: 'msg-1', chatId: harness.chat.id, seq: 1, type: 'user', content: 'Hello', createdAt: 1, status: 'received' })

    harness.view.renderChatHeader()

    expect(harness.chatStatusEl.textContent).toBe('Active')
    expect(harness.chatSubtitleEl.textContent).toBe('Collaborative mode · 1 members · 1 messages')
    expect(harness.togglePeopleDrawerEl.textContent).toBe('Members 1')
  })

  it('toggles manual mention routing for collaborative chats', async () => {
    const harness = createHarness('collaborative')

    harness.view.renderChatHeader()

    const toggle = harness.headerActionsEl.querySelector<HTMLButtonElement>('.manual-mention-toggle')
    expect(toggle).not.toBeNull()
    expect(toggle?.hidden).toBe(false)
    expect(toggle?.textContent).toBe('免@')
    expect(toggle?.title).toBe('开启后，普通消息也会触发所有成员回复；关闭后，只有 @ 成员或 @所有人才触发回复')
    expect(toggle?.getAttribute('aria-pressed')).toBe('false')
    expect(toggle?.getAttribute('aria-checked')).toBe('false')

    toggle?.click()
    await Promise.resolve()

    expect(harness.runCommand).toHaveBeenCalledWith('GROUP_CHAT_UPDATE', { chatId: 'chat-1', requireManualMention: false })

    harness.chat.requireManualMention = false
    harness.view.renderChatHeader()

    expect(toggle?.textContent).toBe('免@')
    expect(toggle?.title).toBe('开启后，普通消息也会触发所有成员回复；关闭后，只有 @ 成员或 @所有人才触发回复')
    expect(toggle?.getAttribute('aria-pressed')).toBe('true')
    expect(toggle?.getAttribute('aria-checked')).toBe('true')

    harness.chat.mode = 'independent'
    harness.view.renderChatHeader()

    expect(toggle?.hidden).toBe(true)
  })
})

function createHarness(mode: RoomMode, language: 'zh-CN' | 'en' = 'zh-CN') {
  const chat: GroupChat = {
    id: 'chat-1',
    name: '群聊',
    mode,
    roleIds: [],
    messageIds: [],
    nextMessageSeq: 1,
    status: 'ready',
    createdAt: 1,
    updatedAt: 1,
  }
  const chatTitleEl = document.createElement('h2')
  const chatSubtitleEl = document.createElement('p')
  const chatStatusEl = document.createElement('span')
  const togglePeopleDrawerEl = document.createElement('button')
  const openOrchestrationEl = document.createElement('button')
  const headerActionsEl = document.createElement('div')
  headerActionsEl.append(togglePeopleDrawerEl)
  const roles: GroupRole[] = []
  const messages: GroupMessage[] = []
  const runCommand = vi.fn(async () => undefined)
  const view = createChatHeaderView({
    state: createTeamPageState(),
    chatTitleEl,
    chatSubtitleEl,
    chatStatusEl,
    togglePeopleDrawerEl,
    openOrchestrationEl,
    getLanguage: () => language,
    getCurrentChat: () => chat,
    getCurrentRoles: () => roles,
    getCurrentMessages: () => messages,
    runCommand,
  })
  return { chat, roles, messages, chatStatusEl, chatSubtitleEl, togglePeopleDrawerEl, openOrchestrationEl, headerActionsEl, runCommand, view }
}
