// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import type { RoleToBackgroundMessage } from '../group/runtimeProtocol'
import { createReplyObserver } from './replyObserver'
import type { RoleSession } from './roleSession'
import type { ChatSiteAdapter } from './sites/types'

describe('createReplyObserver', () => {
  it('uses timeout compensation to report a visible reply instead of marking the role as failed', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content id="old">旧回复</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const adapter = createFakeAdapter({ isGenerating: () => false })
    const reportRoleError = vi.fn()
    const observer = createReplyObserver({
      siteAdapter: adapter,
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError,
    })

    observer.capturePromptReplyBaseline('msg-1')
    roleSession.startPrompt('msg-1', 'attempt-1')
    document.body.insertAdjacentHTML('beforeend', '<message-content id="new">新的回复</message-content>')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(120_000)

    expect(sentMessages).toContainEqual({
      type: 'TEAM_ROLE_REPLY',
      chatId: 'chat-1',
      roleId: 'role-1',
      messageId: 'msg-1',
      replyAttemptId: 'attempt-1',
      content: '新的回复',
      contentFormat: undefined,
      conversationId: 'conv-1',
      conversationUrl: 'https://gemini.google.com/app/conv-1',
    })
    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_STATUS', status: 'error' }))
    expect(reportRoleError).not.toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('reports a same-text reply after a DOM rebuild when it appears after the baseline', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content>好的。</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const observer = createReplyObserver({
      siteAdapter: createFakeAdapter({ isGenerating: () => false }),
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    observer.capturePromptReplyBaseline('msg-2')
    roleSession.startPrompt('msg-2', 'attempt-2')
    document.body.innerHTML = `
      <message-content>好的。</message-content>
      <message-content>好的。</message-content>
    `
    observer.startReplyPolling('msg-2', 'attempt-2')

    await vi.advanceTimersByTimeAsync(8_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_REPLY',
      messageId: 'msg-2',
      content: '好的。',
    }))

    vi.useRealTimers()
  })

  it('reports structured timeout reasons when no reply appears', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = ''

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const reportRoleError = vi.fn()
    const observer = createReplyObserver({
      siteAdapter: createFakeAdapter({ isGenerating: () => false }),
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError,
    })

    roleSession.startPrompt('msg-1', 'attempt-1')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(120_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_STATUS',
      status: 'error',
      error: 'RESPONSE_NOT_FOUND',
    }))
    expect(reportRoleError).toHaveBeenCalledWith('msg-1', 'RESPONSE_NOT_FOUND', undefined, undefined, 'attempt-1')

    vi.useRealTimers()
  })

  it('keeps polling a very short stable reply so a longer continuation can be collected', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content id="new">好的。</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const adapter = createFakeAdapter({ isGenerating: () => false })
    const observer = createReplyObserver({
      siteAdapter: adapter,
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    roleSession.startPrompt('msg-1', 'attempt-1')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(4_000)

    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_REPLY' }))

    document.querySelector('message-content')!.textContent = '好的。这里是完整回复：短回复只是开头，后面还会继续补充关键判断、风险和下一步建议，应该等这一整段内容稳定后再上报。'

    await vi.advanceTimersByTimeAsync(4_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_REPLY',
      content: '好的。这里是完整回复：短回复只是开头，后面还会继续补充关键判断、风险和下一步建议，应该等这一整段内容稳定后再上报。',
    }))

    vi.useRealTimers()
  })

  it('does not report stable partial text while the page is still generating', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content id="new">先输出的一段内容，后面还会继续补充。</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const adapter = createFakeAdapter({ isGenerating: () => true })
    const observer = createReplyObserver({
      siteAdapter: adapter,
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    roleSession.startPrompt('msg-1', 'attempt-1')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(20_000)

    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_REPLY' }))

    vi.useRealTimers()
  })

  it('extends the reply timeout while the page is still generating', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content id="new">先输出的一段内容，仍在思考后续。</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const reportRoleError = vi.fn()
    let generating = true
    const adapter = createFakeAdapter({ isGenerating: () => generating })
    const observer = createReplyObserver({
      siteAdapter: adapter,
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError,
    })

    roleSession.startPrompt('msg-1', 'attempt-1')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(120_000)

    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_REPLY' }))
    expect(sentMessages).toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_STATUS', status: 'generating' }))
    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_STATUS', status: 'error' }))
    expect(reportRoleError).not.toHaveBeenCalled()

    document.querySelector('message-content')!.textContent = '先输出的一段内容，仍在思考后续。现在补充完成。'
    generating = false
    await vi.advanceTimersByTimeAsync(120_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_REPLY',
      messageId: 'msg-1',
      content: '先输出的一段内容，仍在思考后续。现在补充完成。',
    }))
    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_STATUS', status: 'error' }))
    expect(reportRoleError).not.toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('does not send idle for an old reply after a new prompt starts in the same frame', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = ''

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    let finishReplyReport: (() => void) | undefined
    const observer = createReplyObserver({
      siteAdapter: createFakeAdapter({ isGenerating: () => false }),
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        if (message.type === 'TEAM_ROLE_REPLY') {
          await new Promise<void>(resolve => {
            finishReplyReport = resolve
          })
        }
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    observer.capturePromptReplyBaseline('msg-1')
    roleSession.startPrompt('msg-1', 'attempt-1')
    document.body.insertAdjacentHTML('beforeend', '<message-content id="new">第一段工程师回复已经完成，包含需求合理性判断、技术风险、实现步骤、边界条件和下一步建议，准备进入下一个工程师节点继续处理。</message-content>')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(6_000)
    expect(sentMessages).toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_REPLY', messageId: 'msg-1' }))

    roleSession.startPrompt('msg-2', 'attempt-2')
    finishReplyReport?.()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(sentMessages).not.toContainEqual(expect.objectContaining({ type: 'TEAM_ROLE_STATUS', status: 'idle' }))

    vi.useRealTimers()
  })

  it('keeps the stable polling window when the reply text stays the same but DeepSeek-style containers are replaced', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<message-content id="reply-a">这是一个已经完整渲染结束的 DeepSeek 回复内容，文本本身没有继续变化，但容器节点会被虚拟列表替换。</message-content>'

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const adapter = createFakeAdapter({ isGenerating: () => false })
    const observer = createReplyObserver({
      siteAdapter: adapter,
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    roleSession.startPrompt('msg-1', 'attempt-1')
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(2_000)
    document.querySelector('message-content')?.replaceWith(createMessageContent('reply-b', '这是一个已经完整渲染结束的 DeepSeek 回复内容，文本本身没有继续变化，但容器节点会被虚拟列表替换。'))
    await vi.advanceTimersByTimeAsync(2_000)
    document.querySelector('message-content')?.replaceWith(createMessageContent('reply-c', '这是一个已经完整渲染结束的 DeepSeek 回复内容，文本本身没有继续变化，但容器节点会被虚拟列表替换。'))
    await vi.advanceTimersByTimeAsync(2_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_REPLY',
      messageId: 'msg-1',
      content: '这是一个已经完整渲染结束的 DeepSeek 回复内容，文本本身没有继续变化，但容器节点会被虚拟列表替换。',
    }))

    vi.useRealTimers()
  })

  it('reports a new reply when DeepSeek virtual list prunes older baseline containers', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = `
      <message-content id="old-1">历史回复 1</message-content>
      <message-content id="old-2">历史回复 2</message-content>
      <message-content id="old-3">历史回复 3</message-content>
    `

    const sentMessages: RoleToBackgroundMessage[] = []
    const roleSession = createFakeRoleSession()
    const observer = createReplyObserver({
      siteAdapter: createFakeAdapter({ isGenerating: () => false }),
      roleSession,
      log: createFakeLog(),
      sendRuntimeMessage: async message => {
        sentMessages.push(message)
        return { ok: true } as never
      },
      reportRoleError: vi.fn(),
    })

    observer.capturePromptReplyBaseline('msg-1')
    roleSession.startPrompt('msg-1', 'attempt-1')
    document.body.innerHTML = `
      <message-content id="old-3-rebuilt">历史回复 3</message-content>
      <message-content id="new-reply">这是 DeepSeek 在虚拟列表裁剪历史节点后出现的新回复。</message-content>
    `
    observer.startReplyPolling('msg-1', 'attempt-1')

    await vi.advanceTimersByTimeAsync(8_000)

    expect(sentMessages).toContainEqual(expect.objectContaining({
      type: 'TEAM_ROLE_REPLY',
      messageId: 'msg-1',
      content: '这是 DeepSeek 在虚拟列表裁剪历史节点后出现的新回复。',
    }))

    vi.useRealTimers()
  })
})

function createFakeRoleSession(): RoleSession {
  let activeMessageId: string | undefined
  let activeReplyAttemptId: string | undefined

  return {
    getAssignedRole: () => ({ chatId: 'chat-1', roleId: 'role-1', roleName: '工程师' }),
    getActivePrompt: () => (activeMessageId ? { messageId: activeMessageId, replyAttemptId: activeReplyAttemptId } : undefined),
    getActiveMessageId: () => activeMessageId,
    getActiveReplyAttemptId: () => activeReplyAttemptId,
    getAssignedChatId: () => 'chat-1',
    assignRole: vi.fn(),
    startPrompt(messageId, replyAttemptId): void {
      activeMessageId = messageId
      activeReplyAttemptId = replyAttemptId
    },
    clearActivePrompt(messageId): string | undefined {
      if (messageId && activeMessageId !== messageId) return activeReplyAttemptId
      const replyAttemptId = activeReplyAttemptId
      activeMessageId = undefined
      activeReplyAttemptId = undefined
      return replyAttemptId
    },
  }
}

function createFakeAdapter(overrides: Partial<ChatSiteAdapter> = {}): ChatSiteAdapter {
  return {
    id: 'gemini',
    getConversationSnapshot: () => ({ conversationId: 'conv-1', conversationUrl: 'https://gemini.google.com/app/conv-1' }),
    getConversationId: () => 'conv-1',
    getResponseContainers: () => [...document.querySelectorAll('message-content')],
    getAllAssistantReplies: () => [...document.querySelectorAll('message-content')].map(element => element.textContent ?? '').filter(Boolean),
    readResponseText: node => node.textContent ?? '',
    findResponseContainer: element => element?.closest('message-content') ?? null,
    isGenerating: () => true,
    stopGenerating: vi.fn(async () => true),
    fillAndSend: vi.fn(),
    collectPromptDiagnostics: () => ({}),
    ...overrides,
  }
}

function createFakeLog() {
  return {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  }
}

function createMessageContent(id: string, text: string): HTMLElement {
  const element = document.createElement('message-content')
  element.id = id
  element.textContent = text
  return element
}
