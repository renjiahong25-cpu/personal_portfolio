// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createIframeHost, FRAME_ASSIGN_MESSAGE, type FrameAssignmentMessage } from './iframeHost'
import type { GroupChat, GroupRole } from '../group/types'

function makeChat(id: string, roleIds: string[]): GroupChat {
  return {
    id,
    name: id,
    mode: 'independent',
    roleIds,
    messageIds: [],
    nextMessageSeq: 1,
    status: 'ready',
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeRole(chatId: string, id: string, geminiConversationUrl?: string): GroupRole {
  return {
    id,
    chatId,
    name: id,
    status: 'pending',
    contextCursor: 0,
    geminiConversationUrl,
    createdAt: 1,
    updatedAt: 1,
  }
}

function dispatchIframeLoad(iframe: HTMLIFrameElement | undefined): asserts iframe is HTMLIFrameElement {
  expect(iframe).toBeInstanceOf(HTMLIFrameElement)
  if (!iframe) throw new Error('Expected iframe to exist')
  iframe.dispatchEvent(new Event('load'))
}

describe('IframeHost', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    document.body.replaceChildren()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('creates a chat-frame-group for active chat roles and posts chat-scoped role assignment repeatedly', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50, hostTabId: 123 })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])

    const group = host.getChatGroup('chat-1')
    const iframe = host.getRoleFrame('chat-1', 'role-1')
    expect(group).toBeInstanceOf(HTMLElement)
    expect(group?.parentElement).toBe(visibleHost)
    expect(group?.dataset.chatFrameGroup).toBe('true')
    expect(group?.dataset.chatId).toBe('chat-1')
    expect(group?.dataset.activeChat).toBe('true')
    expect(group?.querySelector('.chat-frame-group-title')?.textContent).toBe('chat-1')
    expect(iframe).toBeInstanceOf(HTMLIFrameElement)
    expect(iframe?.parentElement?.classList.contains('role-frame-shell')).toBe(true)
    expect(iframe?.parentElement?.parentElement).toBe(group)
    expect(iframe?.parentElement?.querySelector('.role-frame-name')?.textContent).toBe('role-1')
    expect(iframe?.parentElement?.querySelector('.role-frame-site')?.textContent).toBe('Gemini')
    expect(iframe?.dataset.chatId).toBe('chat-1')
    expect(iframe?.dataset.roleId).toBe('role-1')
    expect(iframe?.dataset.roleKey).toBe('chat-1:role-1')
    expect(iframe?.src).toBe('https://gemini.google.com/')

    const postMessage = vi.spyOn(iframe!.contentWindow!, 'postMessage')
    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(120)

    expect(postMessage).toHaveBeenCalled()
    const lastCall = postMessage.mock.calls[postMessage.mock.calls.length - 1]
    const message = lastCall?.[0] as FrameAssignmentMessage
    expect(message).toEqual({ type: FRAME_ASSIGN_MESSAGE, chatId: 'chat-1', roleId: 'role-1', hostTabId: 123 })
    expect(lastCall?.[1]).toBe('https://gemini.google.com')
    expect(host.getChatState('chat-1')[0].assignmentAttempts).toBeGreaterThan(1)
  })

  it('posts role assignments to the ChatGPT origin for ChatGPT role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50, hostTabId: 123 })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1', 'https://chatgpt.com/c/abc')])

    const iframe = host.getRoleFrame('chat-1', 'role-1')
    expect(iframe?.src).toBe('https://chatgpt.com/c/abc')

    const postMessage = vi.spyOn(iframe!.contentWindow!, 'postMessage')
    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(120)

    const lastCall = postMessage.mock.calls[postMessage.mock.calls.length - 1]
    expect(lastCall?.[1]).toBe('https://chatgpt.com')
  })

  it('shows the role site next to each background iframe label', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1', 'role-2']), [
      { ...makeRole('chat-1', 'role-1'), name: '产品经理', chatSite: 'chatgpt' },
      { ...makeRole('chat-1', 'role-2'), name: '技术方案', chatSite: 'deepseek' },
    ])

    const labels = [...visibleHost.querySelectorAll('.role-frame-label')]
    expect(labels[0].querySelector('.role-frame-name')?.textContent).toBe('产品经理')
    expect(labels[0].querySelector('.role-frame-site')?.textContent).toBe('ChatGPT')
    expect(labels[1].querySelector('.role-frame-name')?.textContent).toBe('技术方案')
    expect(labels[1].querySelector('.role-frame-site')?.textContent).toBe('DeepSeek')
  })

  it('posts role assignments to the Claude origin for Claude role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50, hostTabId: 123 })

    host.activateChat(makeChat('chat-1', ['role-1']), [{ ...makeRole('chat-1', 'role-1', 'https://claude.ai/chat/abc'), chatSite: 'claude' }])

    const iframe = host.getRoleFrame('chat-1', 'role-1')
    expect(iframe?.src).toBe('https://claude.ai/chat/abc')

    const postMessage = vi.spyOn(iframe!.contentWindow!, 'postMessage')
    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(120)

    const lastCall = postMessage.mock.calls[postMessage.mock.calls.length - 1]
    expect(lastCall?.[1]).toBe('https://claude.ai')
  })

  it('posts role assignments to the DeepSeek origin for DeepSeek role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50, hostTabId: 123 })

    host.activateChat(makeChat('chat-1', ['role-1']), [{ ...makeRole('chat-1', 'role-1', 'https://chat.deepseek.com/a/chat/s/abc'), chatSite: 'deepseek' }])

    const iframe = host.getRoleFrame('chat-1', 'role-1')
    expect(iframe?.src).toBe('https://chat.deepseek.com/a/chat/s/abc')

    const postMessage = vi.spyOn(iframe!.contentWindow!, 'postMessage')
    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(120)

    const lastCall = postMessage.mock.calls[postMessage.mock.calls.length - 1]
    expect(lastCall?.[1]).toBe('https://chat.deepseek.com')
  })

  it('uses safe ChatGPT conversation URLs for role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1', 'https://chatgpt.com/c/restored')])

    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://chatgpt.com/c/restored')
  })

  it('uses a ChatGPT GPTs start URL before a role has a conversation URL', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [{
      ...makeRole('chat-1', 'role-1'),
      chatSite: 'chatgpt',
      chatGptGptsUrl: 'https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian',
    }])

    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian')
  })

  it('uses a Grok project start URL before a role has a conversation URL', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [{
      ...makeRole('chat-1', 'role-1'),
      chatSite: 'grok',
      grokProjectUrl: 'https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81',
    }])

    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://grok.com/project/a9e415eb-149b-42b8-811a-63b12477ed81')
  })

  it('uses safe Claude conversation URLs for role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [{ ...makeRole('chat-1', 'role-1', 'https://claude.ai/chat/restored'), chatSite: 'claude' }])

    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://claude.ai/chat/restored')
  })

  it('uses safe DeepSeek conversation URLs for role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [{ ...makeRole('chat-1', 'role-1', 'https://chat.deepseek.com/a/chat/s/restored'), chatSite: 'deepseek' }])

    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://chat.deepseek.com/a/chat/s/restored')
  })

  it('mounts all active chat role iframes in that chat group', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    const chat = makeChat('chat-1', ['role-1', 'role-2'])

    host.activateChat(chat, [makeRole('chat-1', 'role-1'), makeRole('chat-1', 'role-2')])

    const group = host.getChatGroup('chat-1')
    expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.parentElement).toBe(group)
    expect(host.getRoleFrame('chat-1', 'role-2')?.parentElement?.parentElement).toBe(group)
    expect(visibleHost.querySelectorAll('[data-chat-frame-group="true"]')).toHaveLength(1)
    expect(group?.querySelectorAll('iframe')).toHaveLength(2)
    expect(group?.querySelectorAll('.role-frame-label')).toHaveLength(2)
  })

  it('removes role iframes that are no longer members when reactivating a chat', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const onEvent = vi.fn()
    const host = createIframeHost({ visibleHost, onEvent })
    const chat = makeChat('chat-1', ['role-1', 'role-2'])
    host.activateChat(chat, [makeRole('chat-1', 'role-1'), makeRole('chat-1', 'role-2')])
    expect(host.getRoleFrame('chat-1', 'role-2')).toBeInstanceOf(HTMLIFrameElement)

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])

    expect(host.getRoleFrame('chat-1', 'role-1')).toBeInstanceOf(HTMLIFrameElement)
    expect(host.getRoleFrame('chat-1', 'role-2')).toBeUndefined()
    expect(host.getChatGroup('chat-1')?.querySelectorAll('iframe')).toHaveLength(1)
    expect(visibleHost.querySelector('[data-role-key="chat-1:role-2"]')).toBeNull()
    expect(onEvent).toHaveBeenCalledWith({ type: 'role-disposed', chatId: 'chat-1', roleId: 'role-2' })
  })

  it('updates chat group and role frame labels when display names change', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    const chat = { ...makeChat('chat-1', ['role-1']), name: '产品评审群' }
    const role = { ...makeRole('chat-1', 'role-1'), name: '产品经理' }

    host.activateChat(chat, [role])

    expect(host.getChatGroup('chat-1')?.querySelector('.chat-frame-group-title')?.textContent).toBe('产品评审群')
    expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.querySelector('.role-frame-name')?.textContent).toBe('产品经理')

    host.activateChat({ ...chat, name: '增长复盘群' }, [{ ...role, name: '增长顾问' }])

    expect(host.getChatGroup('chat-1')?.querySelector('.chat-frame-group-title')?.textContent).toBe('增长复盘群')
    expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.querySelector('.role-frame-name')?.textContent).toBe('增长顾问')
  })

  it('does not move active chat iframes through a hidden host on same-chat reactivation', () => {
    const visibleHost = document.createElement('div')
    const hiddenHost = document.createElement('div')
    document.body.append(visibleHost, hiddenHost)
    const appendHidden = vi.spyOn(hiddenHost, 'append')
    const host = createIframeHost({ visibleHost, hiddenHost })
    const chat = makeChat('chat-1', ['role-1'])

    host.activateChat(chat, [makeRole('chat-1', 'role-1')])
    const iframe = host.getRoleFrame('chat-1', 'role-1')
    const group = host.getChatGroup('chat-1')
    host.activateChat(chat, [makeRole('chat-1', 'role-1')])

    expect(appendHidden).not.toHaveBeenCalled()
    expect(hiddenHost.isConnected).toBe(false)
    expect(iframe?.parentElement?.parentElement).toBe(group)
  })

  it('skips iframe DOM work when the active chat and role inputs have not changed', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const onEvent = vi.fn()
    const host = createIframeHost({ visibleHost, onEvent })
    const chat = makeChat('chat-1', ['role-1'])
    const role = makeRole('chat-1', 'role-1')

    host.activateChat(chat, [role])
    onEvent.mockClear()
    const state = host.activateChat(chat, [role])

    expect(state).toHaveLength(1)
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('still updates labels when the same active chat receives new display names', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const onEvent = vi.fn()
    const host = createIframeHost({ visibleHost, onEvent })
    const chat = { ...makeChat('chat-1', ['role-1']), name: '旧群名' }
    const role = { ...makeRole('chat-1', 'role-1'), name: '旧角色' }

    host.activateChat(chat, [role])
    onEvent.mockClear()
    host.activateChat({ ...chat, name: '新群名' }, [{ ...role, name: '新角色' }])

    expect(host.getChatGroup('chat-1')?.querySelector('.chat-frame-group-title')?.textContent).toBe('新群名')
    expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.querySelector('.role-frame-name')?.textContent).toBe('新角色')
    expect(onEvent).toHaveBeenCalled()
  })

  it('scrolls the activated chat group into view when switching groups', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const scrollIntoView = vi.fn()
    const originalScrollIntoView = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = scrollIntoView
    const host = createIframeHost({ visibleHost })

    try {
      host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
      scrollIntoView.mockClear()
      host.activateChat(makeChat('chat-2', ['role-2']), [makeRole('chat-2', 'role-2')])
    } finally {
      Element.prototype.scrollIntoView = originalScrollIntoView
    }

    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start', behavior: 'smooth' })
  })

  it('focuses a role frame and highlights its chat group', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const scrollIntoView = vi.fn()
    const originalScrollIntoView = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = scrollIntoView
    const host = createIframeHost({ visibleHost })

    try {
      host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
      host.activateChat(makeChat('chat-2', ['role-2']), [makeRole('chat-2', 'role-2')])
      const focused = host.focusRoleFrame('chat-1', 'role-1')

      expect(focused).toBe(true)
      expect(host.isChatActive('chat-1')).toBe(true)
      expect(host.getChatGroup('chat-1')?.dataset.activeChat).toBe('true')
      expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.classList.contains('jump-highlight')).toBe(true)
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
      vi.advanceTimersByTime(2400)
      expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.classList.contains('jump-highlight')).toBe(false)
    } finally {
      Element.prototype.scrollIntoView = originalScrollIntoView
    }
  })

  it('updates host tab id used in role assignment messages', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50 })
    host.setHostTabId(456)
    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    const iframe = host.getRoleFrame('chat-1', 'role-1')!
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage')

    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(60)

    const lastCall = postMessage.mock.calls[postMessage.mock.calls.length - 1]
    expect(lastCall?.[0]).toEqual({ type: FRAME_ASSIGN_MESSAGE, chatId: 'chat-1', roleId: 'role-1', hostTabId: 456 })
  })

  it('waits for the iframe to load before posting role assignments', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50 })
    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1', 'https://chatgpt.com/c/abc')])
    const iframe = host.getRoleFrame('chat-1', 'role-1')!
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage')

    vi.advanceTimersByTime(150)

    expect(postMessage).not.toHaveBeenCalled()
    expect(host.getChatState('chat-1')[0].assignmentAttempts).toBe(0)
  })

  it('ignores assignment postMessage attempts before the iframe navigates to the target origin', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50 })
    host.activateChat(makeChat('chat-1', ['role-1']), [{ ...makeRole('chat-1', 'role-1'), chatSite: 'deepseek' }])
    const iframe = host.getRoleFrame('chat-1', 'role-1')!
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage').mockImplementation(() => {
      throw new DOMException(
        "Failed to execute 'postMessage' on 'DOMWindow': The target origin provided ('https://chat.deepseek.com') does not match the recipient window's origin ('chrome-extension://test').",
      )
    })

    dispatchIframeLoad(iframe)
    expect(() => vi.advanceTimersByTime(60)).not.toThrow()
    expect(postMessage).toHaveBeenCalled()
    expect(host.getChatState('chat-1')[0].assignmentAttempts).toBeGreaterThan(1)
  })

  it('keeps previously activated chat groups visible in the iframe host when switching chats', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    const chatOneGroup = host.getChatGroup('chat-1')!
    const chatOneFrame = host.getRoleFrame('chat-1', 'role-1')!
    host.activateChat(makeChat('chat-2', ['role-2']), [makeRole('chat-2', 'role-2')])

    expect(host.isChatActivated('chat-1')).toBe(true)
    expect(host.isChatActive('chat-1')).toBe(false)
    expect(host.isChatActive('chat-2')).toBe(true)
    expect(chatOneGroup.parentElement).toBe(visibleHost)
    expect(chatOneGroup.hidden).toBe(false)
    expect(chatOneGroup.style.display).toBe('')
    expect(chatOneGroup.dataset.backgroundChat).toBe('true')
    expect(chatOneFrame.parentElement?.parentElement).toBe(chatOneGroup)
    expect(host.getRoleFrame('chat-2', 'role-2')?.parentElement?.parentElement).toBe(host.getChatGroup('chat-2'))
  })

  it('removes a deleted chat group and all of its role frames', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const onEvent = vi.fn()
    const host = createIframeHost({ visibleHost, onEvent })

    host.activateChat(makeChat('chat-1', ['role-1', 'role-2']), [makeRole('chat-1', 'role-1'), makeRole('chat-1', 'role-2')])
    host.activateChat(makeChat('chat-2', ['role-3']), [makeRole('chat-2', 'role-3')])

    host.removeChat('chat-1')

    expect(host.getChatGroup('chat-1')).toBeUndefined()
    expect(host.getRoleFrame('chat-1', 'role-1')).toBeUndefined()
    expect(host.getRoleFrame('chat-1', 'role-2')).toBeUndefined()
    expect(host.getChatGroup('chat-2')).toBeInstanceOf(HTMLElement)
    expect(visibleHost.querySelectorAll('[data-chat-id="chat-1"]')).toHaveLength(0)
    expect(onEvent).toHaveBeenCalledWith({ type: 'role-disposed', chatId: 'chat-1', roleId: 'role-1' })
    expect(onEvent).toHaveBeenCalledWith({ type: 'role-disposed', chatId: 'chat-1', roleId: 'role-2' })
  })

  it('lists chat groups with active state and role ids', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    host.activateChat(makeChat('chat-2', ['role-2']), [makeRole('chat-2', 'role-2')])

    expect(host.listChatGroups()).toEqual([
      { chatId: 'chat-1', active: false, roleIds: ['role-1'] },
      { chatId: 'chat-2', active: true, roleIds: ['role-2'] },
    ])
  })

  it('restores missing role iframes from safe Gemini URLs without activating inactive chats', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    const chat = makeChat('chat-1', ['safe-role', 'unsafe-role'])

    const state = host.restoreChat(chat, [
      makeRole('chat-1', 'safe-role', 'https://gemini.google.com/app/abc'),
      makeRole('chat-1', 'unsafe-role', 'https://example.com/app/abc'),
    ])

    const group = host.getChatGroup('chat-1')
    expect(host.isChatActivated('chat-1')).toBe(true)
    expect(host.isChatActive('chat-1')).toBe(false)
    expect(group?.dataset.backgroundChat).toBe('true')
    expect(host.getRoleFrame('chat-1', 'safe-role')?.parentElement?.parentElement).toBe(group)
    expect(host.getRoleFrame('chat-1', 'safe-role')?.src).toBe('https://gemini.google.com/app/abc')
    expect(host.getRoleFrame('chat-1', 'unsafe-role')?.src).toBe('https://gemini.google.com/')
    expect(state).toHaveLength(2)
  })

  it('recovers a single role by replacing only that role frame inside its chat group', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    const chat = makeChat('chat-1', ['role-1', 'role-2'])
    host.activateChat(chat, [makeRole('chat-1', 'role-1'), makeRole('chat-1', 'role-2')])
    const oldRoleOneFrame = host.getRoleFrame('chat-1', 'role-1')
    const roleTwoFrame = host.getRoleFrame('chat-1', 'role-2')
    const group = host.getChatGroup('chat-1')

    host.recoverRole(makeRole('chat-1', 'role-1', 'https://gemini.google.com/app/restored'))

    expect(host.getRoleFrame('chat-1', 'role-1')).not.toBe(oldRoleOneFrame)
    expect(host.getRoleFrame('chat-1', 'role-1')?.parentElement?.parentElement).toBe(group)
    expect(host.getRoleFrame('chat-1', 'role-1')?.src).toBe('https://gemini.google.com/app/restored')
    expect(host.getRoleFrame('chat-1', 'role-2')).toBe(roleTwoFrame)
  })

  it('stops assignment loop when a role is marked ready', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost, assignIntervalMs: 50 })
    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    const iframe = host.getRoleFrame('chat-1', 'role-1')!
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage')

    dispatchIframeLoad(iframe)
    vi.advanceTimersByTime(60)
    host.markRoleReady('chat-1', 'role-1')
    const callsAfterReady = postMessage.mock.calls.length
    vi.advanceTimersByTime(150)

    expect(postMessage).toHaveBeenCalled()
    expect(postMessage.mock.calls.length).toBe(callsAfterReady)
    expect(host.getChatState('chat-1')[0].status).toBe('assigned')
  })

  it('does not create role iframes while disabled', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    host.setEnabled(false)

    const state = host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])

    expect(state).toEqual([])
    expect(host.getRoleFrame('chat-1', 'role-1')).toBeUndefined()
    expect(visibleHost.querySelector('iframe')).toBeNull()
  })

  it('removes existing role iframes when disabled', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const host = createIframeHost({ visibleHost })
    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    expect(visibleHost.querySelector('iframe')).not.toBeNull()

    host.setEnabled(false)

    expect(host.getChatState('chat-1')).toEqual([])
    expect(visibleHost.querySelector('iframe')).toBeNull()
  })

  it('emits group and role lifecycle events for runtime diagnostics', () => {
    const visibleHost = document.createElement('div')
    document.body.append(visibleHost)
    const onEvent = vi.fn()
    const host = createIframeHost({ visibleHost, onEvent })

    host.activateChat(makeChat('chat-1', ['role-1']), [makeRole('chat-1', 'role-1')])
    dispatchIframeLoad(host.getRoleFrame('chat-1', 'role-1'))
    host.activateChat({ ...makeChat('chat-1', ['role-1']), name: 'chat-1-renamed' }, [{ ...makeRole('chat-1', 'role-1'), name: 'role-1-renamed' }])
    host.markRoleReady('chat-1', 'role-1')
    host.recoverRole(makeRole('chat-1', 'role-1'))
    host.activateChat(makeChat('chat-2', ['role-2']), [makeRole('chat-2', 'role-2')])

    const eventTypes = onEvent.mock.calls.map(call => call[0].type)
    expect(eventTypes).toContain('group-created')
    expect(eventTypes).toContain('group-highlighted')
    expect(eventTypes).toContain('group-preserved')
    expect(eventTypes).toContain('role-created')
    expect(eventTypes).toContain('role-reused')
    expect(eventTypes).toContain('role-recovered')
    expect(eventTypes).toContain('role-assigned')
    expect(eventTypes).toContain('role-ready')
  })
})
