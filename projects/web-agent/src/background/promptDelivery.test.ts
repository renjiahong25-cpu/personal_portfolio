import { describe, expect, it, vi } from 'vitest'
import { classifyRetryScenario, sendPromptDeliveryWithRetry, RETRY_BUDGET } from './promptDeliveryRetry'

describe('background prompt delivery', () => {
  it('sends a prompt to the target frame and reports failed content responses', async () => {
    vi.resetModules()
    const tabsSendMessage = vi.fn()
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ok: false, error: '输入框不可用' })
    vi.stubGlobal('chrome', { tabs: { sendMessage: tabsSendMessage } })

    const { createPromptSender } = await import('./promptDelivery')
    const log = { info: vi.fn(), warn: vi.fn() }
    const sendPrompt = createPromptSender({ log })
    const delivery = {
      roleId: 'role-1',
      tabId: 101,
      frameId: 7,
      message: {
        type: 'TEAM_SEND_PROMPT' as const,
        chatId: 'chat-1',
        roleId: 'role-1',
        messageId: 'msg-1',
        content: '请分析',
        includesPersona: true,
      },
    }

    await expect(sendPrompt(delivery)).resolves.toBeUndefined()
    await expect(sendPrompt(delivery)).rejects.toThrow('输入框不可用')

    expect(tabsSendMessage).toHaveBeenCalledWith(101, delivery.message, { frameId: 7 })
    expect(log.info).toHaveBeenCalledWith('prompt:send:start', expect.objectContaining({
      chatId: 'chat-1',
      roleId: 'role-1',
      messageId: 'msg-1',
      includesPersona: true,
    }))
    expect(log.warn).toHaveBeenCalledWith('prompt:send:failed', expect.objectContaining({
      chatId: 'chat-1',
      roleId: 'role-1',
      messageId: 'msg-1',
      error: '输入框不可用',
    }))
  })

  it('retries prompt delivery through a shared helper using the latest frame binding', async () => {
    const sendPrompt = vi.fn()
      .mockRejectedValueOnce(new Error('输入框繁忙'))
      .mockResolvedValueOnce(undefined)
    const delivery = {
      roleId: 'role-1',
      tabId: 101,
      frameId: 1,
      message: {
        type: 'TEAM_SEND_PROMPT' as const,
        chatId: 'chat-1',
        roleId: 'role-1',
        messageId: 'msg-1',
        content: '请分析',
      },
    }
    const getLatestBinding = vi.fn()
      .mockReturnValueOnce({ ready: true, tabId: 101, frameId: 1 })
      .mockReturnValueOnce({ ready: true, tabId: 202, frameId: 2 })
    const markDeliveryError = vi.fn(async () => undefined)
    const requestRoleRecovery = vi.fn(async () => true)

    await expect(sendPromptDeliveryWithRetry({
      log: { warn: vi.fn() },
      sendPrompt,
      getLatestBinding,
      isDeliveryStillActive: vi.fn(async () => true),
      markDeliveryError,
      requestRoleRecovery,
      waitForRetry: vi.fn(async () => undefined),
    }, {
      chatId: 'chat-1',
      messageId: 'msg-1',
      delivery,
      retryDelaysMs: [0],
    })).resolves.toBe(true)

    expect(sendPrompt).toHaveBeenNthCalledWith(1, expect.objectContaining({ tabId: 101, frameId: 1 }))
    expect(sendPrompt).toHaveBeenNthCalledWith(2, expect.objectContaining({ tabId: 202, frameId: 2 }))
    expect(requestRoleRecovery).toHaveBeenCalledWith('chat-1', 'role-1', '输入框繁忙')
    expect(markDeliveryError).not.toHaveBeenCalled()
  })
})

describe('Bug 3: retry classification and budget', () => {
  it('classifyRetryScenario returns resend for non-retry', () => {
    expect(classifyRetryScenario({
      isRetry: false,
      attemptIndex: 0,
      maxAttempts: 3,
      promptAlreadyDelivered: false,
      replyContentAppeared: false,
    })).toBe('resend')
  })

  it('classifyRetryScenario returns budget-exhausted when attempts exceeded', () => {
    expect(classifyRetryScenario({
      isRetry: true,
      attemptIndex: 3,
      maxAttempts: 3,
      promptAlreadyDelivered: false,
      replyContentAppeared: false,
    })).toBe('budget-exhausted')
  })

  it('classifyRetryScenario returns collect-existing when reply already appeared', () => {
    expect(classifyRetryScenario({
      isRetry: true,
      attemptIndex: 0,
      maxAttempts: 3,
      promptAlreadyDelivered: true,
      replyContentAppeared: true,
    })).toBe('collect-existing')
  })

  it('classifyRetryScenario returns skip-send-wait-reply when prompt delivered but no reply', () => {
    expect(classifyRetryScenario({
      isRetry: true,
      attemptIndex: 0,
      maxAttempts: 3,
      promptAlreadyDelivered: true,
      replyContentAppeared: false,
    })).toBe('skip-send-wait-reply')
  })

  it('classifyRetryScenario returns resend when prompt not delivered', () => {
    expect(classifyRetryScenario({
      isRetry: true,
      attemptIndex: 0,
      maxAttempts: 3,
      promptAlreadyDelivered: false,
      replyContentAppeared: false,
    })).toBe('resend')
  })

  it('marks isRetry on the message during retry attempts', async () => {
    const sendPrompt = vi.fn()
      .mockRejectedValueOnce(new Error('连接断开'))
      .mockResolvedValueOnce(undefined)
    const delivery = {
      roleId: 'role-1',
      tabId: 101,
      frameId: 1,
      message: {
        type: 'TEAM_SEND_PROMPT' as const,
        chatId: 'chat-1',
        roleId: 'role-1',
        messageId: 'msg-1',
        content: '请分析',
      },
    }

    await sendPromptDeliveryWithRetry({
      log: { warn: vi.fn() },
      sendPrompt,
      getLatestBinding: vi.fn().mockReturnValue({ ready: true, tabId: 101, frameId: 1 }),
      isDeliveryStillActive: vi.fn(async () => true),
      markDeliveryError: vi.fn(async () => undefined),
      waitForRetry: vi.fn(async () => undefined),
    }, {
      chatId: 'chat-1',
      messageId: 'msg-1',
      delivery,
      retryDelaysMs: [0],
      isRetry: true,
    })

    // First attempt should have isRetry: true since input.isRetry is true
    expect(sendPrompt).toHaveBeenNthCalledWith(1, expect.objectContaining({
      message: expect.objectContaining({ isRetry: true }),
    }))
    // Second attempt should also have isRetry: true
    expect(sendPrompt).toHaveBeenNthCalledWith(2, expect.objectContaining({
      message: expect.objectContaining({ isRetry: true }),
    }))
  })

  it('respects retry budget and stops after exceeding it', async () => {
    const sendPrompt = vi.fn().mockRejectedValue(new Error('连接断开'))
    const delivery = {
      roleId: 'role-1',
      tabId: 101,
      frameId: 1,
      message: {
        type: 'TEAM_SEND_PROMPT' as const,
        chatId: 'chat-1',
        roleId: 'role-1',
        messageId: 'msg-1',
        content: '请分析',
      },
    }
    const markDeliveryError = vi.fn(async () => undefined)

    // Use enough retry delays but isRetry should cap at RETRY_BUDGET.promptDeliveryRetry
    const result = await sendPromptDeliveryWithRetry({
      log: { warn: vi.fn() },
      sendPrompt,
      getLatestBinding: vi.fn().mockReturnValue({ ready: true, tabId: 101, frameId: 1 }),
      isDeliveryStillActive: vi.fn(async () => true),
      markDeliveryError,
      waitForRetry: vi.fn(async () => undefined),
    }, {
      chatId: 'chat-1',
      messageId: 'msg-1',
      delivery,
      retryDelaysMs: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      isRetry: true,
    })

    // Should fail because all attempts exhaust the budget
    expect(result).toBe(false)
    expect(markDeliveryError).toHaveBeenCalled()
    // Should not have tried more than RETRY_BUDGET.promptDeliveryRetry + 1 attempts
    expect(sendPrompt.mock.calls.length).toBeLessThanOrEqual(RETRY_BUDGET.promptDeliveryRetry + 1)
  })
})
