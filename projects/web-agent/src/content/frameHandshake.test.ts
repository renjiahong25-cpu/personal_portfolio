// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import type { RoleToBackgroundMessage } from '../group/runtimeProtocol'
import type { ChatSiteAdapter } from './sites/types'
import type { RoleSession } from './roleSession'
import type { ContentLogger } from './runtimeClient'
import { handleRuntimeFrameAssignment, performFrameRoleAssignment, registerFrameRoleHandshake, type FrameRoleHandshakeOptions } from './frameHandshake'

function createHarness(readyResponse: unknown = { ok: true, role: { id: 'role-1', name: 'DEEPSEEK', chatId: 'chat-1' }, replyHistory: ['历史回复'] }, sendError?: string) {
  const siteAdapter = {
    getConversationSnapshot: () => ({ conversationId: 'conv-1', conversationUrl: 'https://chat.deepseek.com/a/chat/s/conv-1' }),
    getConversationId: () => 'conv-1',
  } as unknown as ChatSiteAdapter
  const assignRole = vi.fn()
  const roleSession = { assignRole } as unknown as RoleSession
  const log = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() }
  const seedStoredRoleReplies = vi.fn()
  const sendRuntimeMessage = vi.fn(async (_message: RoleToBackgroundMessage): Promise<unknown> => {
    if (sendError) throw new Error(sendError)
    return readyResponse
  })

  const options = {
    siteAdapter,
    roleSession,
    log: log as unknown as ContentLogger,
    seedStoredRoleReplies,
    sendRuntimeMessage,
  } as FrameRoleHandshakeOptions

  return { options, assignRole, log, seedStoredRoleReplies, sendRuntimeMessage }
}

describe('frame role handshake', () => {
  it('reports the role ready and assigns it on success', async () => {
    const { options, assignRole, seedStoredRoleReplies, sendRuntimeMessage } = createHarness()

    performFrameRoleAssignment(options, 'chat-1', 'role-1')
    await vi.waitFor(() => expect(assignRole).toHaveBeenCalled())

    expect(sendRuntimeMessage).toHaveBeenCalledWith({
      type: 'TEAM_FRAME_ROLE_READY',
      chatId: 'chat-1',
      roleId: 'role-1',
      hostTabId: undefined,
      conversationId: 'conv-1',
      conversationUrl: 'https://chat.deepseek.com/a/chat/s/conv-1',
    })
    expect(assignRole).toHaveBeenCalledWith({
      chatId: 'chat-1',
      roleId: 'role-1',
      roleName: 'DEEPSEEK',
      roomId: 'chat-1',
    })
    expect(seedStoredRoleReplies).toHaveBeenCalledWith(['历史回复'])
  })

  it('ignores assignments without a role id', async () => {
    const { options, assignRole, sendRuntimeMessage } = createHarness()

    performFrameRoleAssignment(options, 'chat-1', '')

    await new Promise(resolve => setTimeout(resolve, 0))
    expect(sendRuntimeMessage).not.toHaveBeenCalled()
    expect(assignRole).not.toHaveBeenCalled()
  })

  it('does not assign the role when the ready handshake fails', async () => {
    const { options, assignRole, log } = createHarness({ ok: false, error: '找不到人员' })

    performFrameRoleAssignment(options, 'chat-1', 'role-1')
    await vi.waitFor(() => expect(log.warn).toHaveBeenCalled())

    expect(assignRole).not.toHaveBeenCalled()
    expect(log.warn).toHaveBeenCalledWith('frame-role:ready-failed', expect.objectContaining({ error: '找不到人员' }))
  })

  it('survives a ready handshake network error', async () => {
    const { options, assignRole, log } = createHarness(undefined, 'extension disconnected')

    performFrameRoleAssignment(options, 'chat-1', 'role-1')
    await vi.waitFor(() => expect(log.warn).toHaveBeenCalledWith('frame-role:ready-error', expect.objectContaining({ error: 'extension disconnected' })))

    expect(assignRole).not.toHaveBeenCalled()
  })

  it('handles runtime assignment messages from the background for plain tabs', async () => {
    const { options, assignRole, sendRuntimeMessage } = createHarness({ ok: true, role: { id: 'role-9', name: '豆包', chatId: 'chat-9' } })

    handleRuntimeFrameAssignment(options, { type: 'WEB_AGENT_ASSIGN_FRAME_ROLE', chatId: 'chat-9', roleId: 'role-9' })
    await vi.waitFor(() => expect(assignRole).toHaveBeenCalled())

    expect(sendRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-9', roleId: 'role-9' }))
    expect(assignRole).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-9', roleId: 'role-9' }))
  })

  it('accepts the legacy roomId field in runtime assignment messages', async () => {
    const { options, sendRuntimeMessage } = createHarness({ ok: true, role: { id: 'role-8', name: '角色', chatId: 'chat-8' } })

    handleRuntimeFrameAssignment(options, { type: 'WEB_AGENT_ASSIGN_FRAME_ROLE', roomId: 'chat-8', roleId: 'role-8' })
    await vi.waitFor(() => expect(sendRuntimeMessage).toHaveBeenCalledTimes(1))

    expect(sendRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-8', roleId: 'role-8' }))
  })

  it('keeps listening to window assignment posts from the iframe host', async () => {
    const { options, assignRole, sendRuntimeMessage } = createHarness({ ok: true, role: { id: 'role-2', name: '角色', chatId: 'chat-2' } })
    registerFrameRoleHandshake(options)

    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'WEB_AGENT_ASSIGN_FRAME_ROLE', chatId: 'chat-2', roomId: 'chat-2', roleId: 'role-2', hostTabId: 77 },
    }))
    await vi.waitFor(() => expect(assignRole).toHaveBeenCalled())

    expect(sendRuntimeMessage).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'chat-2', roleId: 'role-2', hostTabId: 77 }))
    expect(assignRole).toHaveBeenCalled()
  })
})
