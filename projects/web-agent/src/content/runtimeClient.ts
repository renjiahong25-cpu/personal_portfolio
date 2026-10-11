import type { BackgroundToRoleMessage, RoleToBackgroundMessage } from '../group/runtimeProtocol'
import { createLogger, type WebAgentLogger } from '../shared/logger'

export type ContentRuntimeMessage = BackgroundToRoleMessage

export type ContentLogger = Pick<WebAgentLogger, 'debug' | 'info' | 'warn'>

export const contentLog: ContentLogger = createLogger('content')

export async function sendRuntimeMessage<T>(
  message: RoleToBackgroundMessage,
  log: ContentLogger = contentLog,
): Promise<T> {
  return new Promise((resolve, reject) => {
    log.debug('runtime-send:start', { type: message.type })
    chrome.runtime.sendMessage(message, response => {
      const error = chrome.runtime.lastError
      if (error) {
        log.warn('runtime-send:failed', { type: message.type, error: error.message })
        reject(new Error(error.message))
        return
      }

      log.debug('runtime-send:response', { type: message.type, response })
      resolve(response as T)
    })
  })
}
