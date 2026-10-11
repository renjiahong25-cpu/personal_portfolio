import type { WebAgentStore } from '../group/types'
import type { WebAgentControlConnectionStatus } from '../shared/localControlProtocol'
import { createLogger } from '../shared/logger'

export type RuntimeMessage = { type?: string; [key: string]: unknown }

export interface BroadcastStoreUpdatedOptions {
  excludeTabId?: number
}

export const GROUP_PUSH_TYPE = 'WEB_AGENT_GROUP_PUSH'

const hostTabIds = new Set<number>()

const log = createLogger('background')

export function senderTabId(sender: chrome.runtime.MessageSender): number | undefined {
  return sender.tab?.id
}

export function senderFrameId(sender: chrome.runtime.MessageSender): number {
  return sender.frameId ?? 0
}

export function explicitTabId(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}

export function messageTabId(message: RuntimeMessage, sender: chrome.runtime.MessageSender): number | undefined {
  return senderTabId(sender) ?? explicitTabId(message.hostTabId)
}

export function rememberHost(sender: chrome.runtime.MessageSender, explicitTabIdValue?: unknown): void {
  const tabId = senderTabId(sender) ?? explicitTabId(explicitTabIdValue)
  if (tabId !== undefined) hostTabIds.add(tabId)
}

export function forgetHostTab(tabId: number): void {
  hostTabIds.delete(tabId)
}

export function listHostTabIds(): number[] {
  return [...hostTabIds]
}

export async function broadcastStoreUpdated(store: WebAgentStore, options: BroadcastStoreUpdatedOptions = {}): Promise<void> {
  const message = { type: 'GROUP_STORE_UPDATED', store }

  for (const tabId of listHostTabIds()) {
    if (tabId === options.excludeTabId) continue
    try {
      await chrome.tabs.sendMessage(tabId, message)
    } catch (error) {
      log.debug('group-store-updated:tab-failed', { tabId, error: error instanceof Error ? error.message : String(error) })
      forgetHostTab(tabId)
    }
  }

  try {
    await chrome.runtime.sendMessage({ type: GROUP_PUSH_TYPE, payload: message })
  } catch (error) {
    log.debug('group-store-updated:runtime-failed', { error: error instanceof Error ? error.message : String(error) })
  }
}

export async function broadcastControlStatusUpdated(controlStatus: WebAgentControlConnectionStatus): Promise<void> {
  const message = { type: 'GROUP_CONTROL_STATUS_UPDATED', controlStatus }

  for (const tabId of listHostTabIds()) {
    try {
      await chrome.tabs.sendMessage(tabId, message)
    } catch (error) {
      log.debug('control-status-updated:tab-failed', { tabId, error: error instanceof Error ? error.message : String(error) })
      forgetHostTab(tabId)
    }
  }

  try {
    await chrome.runtime.sendMessage({ type: GROUP_PUSH_TYPE, payload: message })
  } catch (error) {
    log.debug('control-status-updated:runtime-failed', { error: error instanceof Error ? error.message : String(error) })
  }
}

export async function sendError(message: string): Promise<void> {
  const payload = { type: 'GROUP_DELIVERY_ERROR', message }
  for (const tabId of listHostTabIds()) {
    try {
      await chrome.tabs.sendMessage(tabId, payload)
    } catch {
      forgetHostTab(tabId)
    }
  }

  try {
    await chrome.runtime.sendMessage({ type: GROUP_PUSH_TYPE, payload })
  } catch {
    // no active receiver
  }
}

export async function requestRoleRecovery(chatId: string, roleId: string, reason?: string): Promise<boolean> {
  const payload = { type: 'GROUP_ROLE_RECOVERY_REQUEST', chatId, roleId, reason }
  log.warn('orchestration-diagnostic:role-recovery-request:start', { chatId, roleId, reason, hostTabIds: listHostTabIds() })
  for (const tabId of listHostTabIds()) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, payload)
      log.warn('orchestration-diagnostic:role-recovery-request:response', { chatId, roleId, tabId, response })
      if (isRecord(response) && response.ok === true) return true
    } catch (error) {
      log.warn('orchestration-diagnostic:role-recovery-request:tab-failed', { tabId, chatId, roleId, error: error instanceof Error ? error.message : String(error) })
      forgetHostTab(tabId)
    }
  }
  log.warn('orchestration-diagnostic:role-recovery-request:not-recovered', { chatId, roleId, reason })
  return false
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
