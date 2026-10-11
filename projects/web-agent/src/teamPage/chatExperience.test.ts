import { describe, expect, it } from 'vitest'
import type { GroupChat, GroupMessage, GroupRole } from '../group/types'
import { buildChatRenderItems, formatChatListTime, getAvatarInitial, getChatStartupNotice, getVisibleThinkingRoles, isThinkingBubbleVisible, isUnavailableRolesError, shouldAutoReconnectRole, shouldConfirmMentionWithEnter, shouldSendMessageWithEnter, THINKING_TIMEOUT_MS } from './chatExperience'

const baseRole: GroupRole = {
  id: 'role-1',
  chatId: 'chat-1',
  name: '工程师',
  status: 'thinking',
  contextCursor: 0,
  updatedAt: 1_000,
  createdAt: 1_000,
}

const baseChat: GroupChat = {
  id: 'chat-1',
  name: '产品评审群',
  mode: 'independent',
  roleIds: ['role-1'],
  messageIds: [],
  nextMessageSeq: 1,
  status: 'ready',
  createdAt: 1_000,
  updatedAt: 1_000,
}

const baseMessage: GroupMessage = {
  id: 'msg-1',
  chatId: 'chat-1',
  seq: 1,
  type: 'assistant',
  roleId: 'role-1',
  roleName: '工程师',
  content: '收到',
  createdAt: 1_000,
  status: 'sent',
}

describe('chat experience helpers', () => {
  it('uses one user-perceived character for avatar initials', () => {
    expect(getAvatarInitial('产品经理')).toBe('产')
    expect(getAvatarInitial('👩‍💻 工程师')).toBe('👩‍💻')
    expect(getAvatarInitial(' Alice ')).toBe('A')
  })

  it('formats chat list timestamps like a compact conversation list', () => {
    const now = new Date(2026, 4, 2, 12, 0).getTime()

    expect(formatChatListTime(new Date(2026, 4, 2, 9, 35).getTime(), now)).toBe('09:35')
    expect(formatChatListTime(new Date(2026, 4, 1, 23, 13).getTime(), now)).toBe('昨天')
    expect(formatChatListTime(new Date(2026, 3, 30, 9, 18).getTime(), now)).toBe('前天')
    expect(formatChatListTime(new Date(2026, 3, 29, 21, 17).getTime(), now)).toBe('04/29')
  })

  it('builds WeChat-style render items with time dividers and compact repeat messages', () => {
    const items = buildChatRenderItems(
      [
        baseMessage,
        { ...baseMessage, id: 'msg-2', seq: 2, content: '补充一句', createdAt: 2_000 },
        { ...baseMessage, id: 'msg-3', seq: 3, type: 'user', roleId: undefined, roleName: undefined, content: '谢谢', createdAt: 400_000 },
      ],
      [baseRole],
      400_000,
    )

    expect(items.map(item => item.type)).toEqual(['time', 'message', 'message', 'time', 'message'])
    expect(items[1]).toMatchObject({ type: 'message', showName: true, showAvatar: true })
    expect(items[2]).toMatchObject({ type: 'message', showName: false, showAvatar: false })
    expect(items[4]).toMatchObject({ type: 'message', showName: false, showAvatar: true })
  })

  it('sends on Enter while leaving Command/Control+Enter for newlines', () => {
    expect(shouldSendMessageWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: false })).toBe(true)
    expect(shouldSendMessageWithEnter({ key: 'Enter', shiftKey: false, metaKey: true, ctrlKey: false })).toBe(false)
    expect(shouldSendMessageWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: true })).toBe(false)
    expect(shouldSendMessageWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: false, isComposing: true } as KeyboardEvent)).toBe(false)
  })

  it('confirms mention options with plain Enter only', () => {
    expect(shouldConfirmMentionWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: false })).toBe(true)
    expect(shouldConfirmMentionWithEnter({ key: 'Enter', shiftKey: false, metaKey: true, ctrlKey: false })).toBe(false)
    expect(shouldConfirmMentionWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: true })).toBe(false)
    expect(shouldConfirmMentionWithEnter({ key: 'Enter', shiftKey: false, metaKey: false, ctrlKey: false, isComposing: true } as KeyboardEvent)).toBe(false)
  })

  it('shows one thinking bubble per thinking role until the 120-second timeout', () => {
    const visibleRole = { ...baseRole, id: 'role-visible', updatedAt: 10_000 }
    const timedOutRole = { ...baseRole, id: 'role-timeout', updatedAt: 10_000 - THINKING_TIMEOUT_MS }
    const readyRole = { ...baseRole, id: 'role-ready', status: 'ready' as const, updatedAt: 10_000 }

    expect(isThinkingBubbleVisible(visibleRole, 10_000 + THINKING_TIMEOUT_MS - 1)).toBe(true)
    expect(isThinkingBubbleVisible(timedOutRole, 10_000)).toBe(false)
    expect(getVisibleThinkingRoles([visibleRole, timedOutRole, readyRole], 10_000).map(role => role.id)).toEqual(['role-visible'])
  })

  it('auto-reconnects unavailable roles without interrupting active thinking roles', () => {
    const now = 10_000

    expect(shouldAutoReconnectRole({ ...baseRole, status: 'pending', updatedAt: now }, now)).toBe(true)
    expect(shouldAutoReconnectRole({ ...baseRole, status: 'loading', updatedAt: now }, now)).toBe(true)
    expect(shouldAutoReconnectRole({ ...baseRole, status: 'error', updatedAt: now }, now)).toBe(true)
    expect(shouldAutoReconnectRole({ ...baseRole, status: 'ready', updatedAt: now }, now)).toBe(false)
    expect(shouldAutoReconnectRole({ ...baseRole, modelSource: 'external', status: 'error', updatedAt: now }, now)).toBe(false)
    expect(shouldAutoReconnectRole({ ...baseRole, status: 'thinking', updatedAt: now }, now + THINKING_TIMEOUT_MS - 1)).toBe(false)
    expect(shouldAutoReconnectRole({ ...baseRole, status: 'thinking', updatedAt: now }, now + THINKING_TIMEOUT_MS)).toBe(true)
  })

  it('detects unavailable-role delivery errors for automatic reconnect', () => {
    expect(isUnavailableRolesError('以下人员不可用，请等待或恢复：程序员')).toBe(true)
    expect(isUnavailableRolesError('Gemini 发送按钮暂不可用，请稍后重试')).toBe(false)
  })

  it('shows a startup notice while a chat is initializing roles', () => {
    expect(getChatStartupNotice({ ...baseChat, status: 'initializing' }, [{ ...baseRole, status: 'pending' }])).toEqual({
      title: '正在初始化角色',
      body: '正在创建角色窗口，准备好后就可以继续对话。',
    })
    expect(getChatStartupNotice(baseChat, [{ ...baseRole, status: 'loading' }])?.title).toBe('正在初始化角色')
    expect(getChatStartupNotice(baseChat, [{ ...baseRole, status: 'ready' }])).toBeUndefined()
  })
})
