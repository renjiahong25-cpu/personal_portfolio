import { afterEach, describe, expect, it, vi } from 'vitest'
import { createLogger } from './logger'

describe('createLogger', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('suppresses all console logs when debug logging is disabled', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined)
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const logger = createLogger('test', {}, { debugEnabled: false })

    logger.debug('hidden-debug', { chatId: 'chat-1' })
    logger.info('hidden-info', { roleId: 'role-1' })
    logger.warn('hidden-warn', { messageId: 'msg-1' })

    expect(debug).not.toHaveBeenCalled()
    expect(info).not.toHaveBeenCalled()
    expect(warn).not.toHaveBeenCalled()
  })

  it('merges child context into emitted details', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined)
    const logger = createLogger('test', { chatId: 'chat-1' }, { debugEnabled: true }).child({ roleId: 'role-1' })

    logger.debug('event', { messageId: 'msg-1' })

    expect(debug).toHaveBeenCalledWith('[Web Agent][test] event', {
      chatId: 'chat-1',
      roleId: 'role-1',
      messageId: 'msg-1',
    })
  })

  it('keeps production error logs off the console', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const logger = createLogger('test', {}, { debugEnabled: false })

    logger.error('hidden-runtime-error', { message: 'failed' })

    expect(error).not.toHaveBeenCalled()
    expect(warn).not.toHaveBeenCalled()
  })

  it('emits warnings and errors when debug logging is enabled', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const logger = createLogger('test', {}, { debugEnabled: true })

    logger.warn('visible-warn', { warning: true })
    logger.error('visible-error', { error: true })

    expect(warn).toHaveBeenCalledWith('[Web Agent][test] visible-warn', { warning: true })
    expect(error).toHaveBeenCalledWith('[Web Agent][test] visible-error', { error: true })
  })
})
