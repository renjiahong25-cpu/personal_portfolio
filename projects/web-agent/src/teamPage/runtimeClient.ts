import type { WebAgentStore } from '../group/types'
import type { WebAgentControlConnectionStatus } from '../shared/localControlProtocol'

export interface RuntimeResponse<T = unknown> {
  ok?: boolean
  error?: string
  store?: WebAgentStore
  controlStatus?: WebAgentControlConnectionStatus
  data?: T
}

export type StorePushMessage =
  | { type: 'GROUP_STORE_UPDATED'; store: WebAgentStore }
  | { type: 'GROUP_CONTROL_STATUS_UPDATED'; controlStatus: WebAgentControlConnectionStatus }
  | { type: 'GROUP_ORCHESTRATION_AUTO_STREAM_CHUNK'; streamId: string; chunk?: string; content?: string }
  | { type: 'GROUP_ROLE_STATUS_UPDATED'; store?: WebAgentStore }
  | { type: 'GROUP_MESSAGE_DELIVERED'; store?: WebAgentStore }
  | { type: 'GROUP_MESSAGE_RECEIVED'; store?: WebAgentStore }
  | { type: 'GROUP_DELIVERY_ERROR'; store?: WebAgentStore; error?: string; message?: string }
  | { type: 'GROUP_ROLE_RECOVERY_REQUEST'; chatId: string; roleId: string; reason?: string }
  | { type: 'TEAM_FRAME_ROLE_READY'; chatId: string; roleId: string; store?: WebAgentStore }

export interface TeamPageRuntimeClientDependencies {
  getHostTabId(): number | undefined
  applyStore(store: WebAgentStore): void
  refreshStore(showFailure?: boolean): Promise<void>
  log: {
    debug(event: string, details?: Record<string, unknown>): void
    warn(event: string, details?: Record<string, unknown>): void
  }
}

export interface TeamPageRuntimeClient {
  sendRuntimeMessage<T>(type: string, payload?: Record<string, unknown>): Promise<RuntimeResponse<T>>
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
}

export function createTeamPageRuntimeClient(deps: TeamPageRuntimeClientDependencies): TeamPageRuntimeClient {
  const sendRuntimeMessage = <T>(type: string, payload: Record<string, unknown> = {}): Promise<RuntimeResponse<T>> => {
    return new Promise((resolve, reject) => {
      const message: Record<string, unknown> = { type, ...payload }
      const hostTabId = deps.getHostTabId()
      if (hostTabId !== undefined && typeof message.hostTabId !== 'number') message.hostTabId = hostTabId
      deps.log.debug('runtime-send:start', { type, hostTabId: message.hostTabId })

      chrome.runtime.sendMessage(message, response => {
        const lastError = chrome.runtime.lastError
        if (lastError) {
          deps.log.warn('runtime-send:failed', { type, error: lastError.message })
          reject(new Error(lastError.message))
          return
        }

        deps.log.debug('runtime-send:response', { type, ok: response?.ok, error: response?.error })
        resolve((response ?? {}) as RuntimeResponse<T>)
      })
    })
  }

  const runCommand = async (type: string, payload: Record<string, unknown> = {}): Promise<void> => {
    const response = await sendRuntimeMessage(type, payload)
    if (response.ok === false) throw new Error(response.error || `${type} failed`)
    if (response.store) {
      deps.applyStore(response.store)
      return
    }
    await deps.refreshStore(false)
  }

  return { sendRuntimeMessage, runCommand }
}
