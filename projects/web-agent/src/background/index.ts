import { loadStore } from '../group/store'
import type { ChatSite, GroupChat, WebAgentStore } from '../group/types'
import { getDefaultChatSiteUrlForRole, getSupportedChatOriginForSite } from '../group/conversationUrl'
import { createChatHandlers } from './chatHandlers'
import { createMessageHandlers } from './messageHandlers'
import {
  broadcastControlStatusUpdated as broadcastRuntimeControlStatusUpdated,
  broadcastStoreUpdated as broadcastRuntimeStoreUpdated,
  forgetHostTab,
  rememberHost,
  requestRoleRecovery,
  sendError,
  type RuntimeMessage,
} from './runtimeClient'
import { createMessageRouter } from './messageRouter'
import { createExternalModelHandlers } from './externalModelHandlers'
import { createExternalModelClient } from './externalModelClient'
import { createPromptSender } from './promptDelivery'
import { createRoleHandlers } from './roleHandlers'
import { createOrchestrationHandlers, type OrchestrationAutoStreamMessage } from './orchestrationHandlers'
import { createRuntimeFrameRegistry } from './runtimeFrames'
import { createSitePromptDeliveryLimiter } from './sitePromptDeliveryLimiter'
import { getChatRoles, mutateStore } from './storeAccess'
import { createLogger } from '../shared/logger'
import type { BackgroundToRoleMessage } from '../group/runtimeProtocol'
import { createControlActionExecutor } from './controlHandlers'
import { createSiteFrameBroker } from './siteFrameBroker'
import { createControlClient } from './controlClient'
import { createSiteStatusRegistry } from './siteStatus'

const runtimeFrames = createRuntimeFrameRegistry()
const log = createLogger('background')
const siteStatus = createSiteStatusRegistry()
const CONTROL_KEEPALIVE_ALARM = 'web-agent-control-keepalive'
const CONTROL_KEEPALIVE_PERIOD_MINUTES = 0.4

const sendPrompt = createPromptSender({ log })
const promptDeliveryLimiter = createSitePromptDeliveryLimiter({ log })
const externalModelClient = createExternalModelClient()

function sendRoleMessage(tabId: number, frameId: number, message: BackgroundToRoleMessage): Promise<unknown> {
  return chrome.tabs.sendMessage(tabId, message, { frameId })
}

function now(): number {
  return Date.now()
}

function newId(prefix: string): string {
  const cryptoApi = globalThis.crypto as Crypto | undefined
  return `${prefix}-${cryptoApi?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

async function broadcastStoreUpdated(store: WebAgentStore, excludeTabId?: number): Promise<void> {
  await broadcastRuntimeStoreUpdated(store, { excludeTabId })
}

async function broadcastAutoGenerateStream(message: OrchestrationAutoStreamMessage): Promise<void> {
  try {
    await chrome.runtime.sendMessage(message)
  } catch (error) {
    log.debug('auto-orchestration-stream:runtime-failed', { error: error instanceof Error ? error.message : String(error) })
  }
}

function getChatStatusFromRoles(store: WebAgentStore, chat: GroupChat): GroupChat['status'] {
  const roles = getChatRoles(store, chat)
  if (roles.length === 0) return 'draft'
  if (roles.some(role => role.status === 'thinking' || role.status === 'loading')) return 'running'
  if (roles.some(role => role.status === 'error')) return 'error'
  return 'ready'
}

async function recoverSiteRolesAfterLogin(siteId: string): Promise<void> {
  const store = await loadStore()
  const recoverable = Object.values(store.rolesById).filter(role => {
    if (role.chatSite !== siteId) return false
    return role.status === 'error' || role.status === 'stopped' || Boolean(role.lastPromptMessageId)
  })
  if (recoverable.length === 0) return
  log.info('site-status:login-recovery', { siteId, roles: recoverable.length })
  for (const role of recoverable) {
    await requestRoleRecovery(role.chatId, role.id, '站点登录完成').catch(() => undefined)
  }
}

async function handleStoreGet(message: RuntimeMessage, sender: chrome.runtime.MessageSender) {
  rememberHost(sender, message.hostTabId)
  const store = await loadStore()
  return { ok: true, store, bindings: runtimeFrames.list(), controlStatus: controlClient.status() }
}

async function handleSettingsUpdate(message: RuntimeMessage) {
  const { store } = await mutateStore(store => {
    const defaultChatSite = readOptionalString(message.defaultChatSite)
    if (defaultChatSite === 'chatgpt' || defaultChatSite === 'gemini' || defaultChatSite === 'claude' || defaultChatSite === 'deepseek' || defaultChatSite === 'doubao' || defaultChatSite === 'grok') {
      store.settings.defaultChatSite = defaultChatSite
    }
    if (typeof message.agentControlEnabled === 'boolean') {
      store.settings.agentControlEnabled = message.agentControlEnabled
    }
    if (typeof message.agentControlPort === 'number' && Number.isInteger(message.agentControlPort) && message.agentControlPort >= 1024 && message.agentControlPort <= 65535) {
      store.settings.agentControlPort = message.agentControlPort
    }
    const language = readOptionalString(message.language)
    if (language === 'en' || language === 'zh-CN') {
      store.settings.language = language
    }
  })
  await broadcastStoreUpdated(store)
  syncControlClient().catch(error => log.warn('control-client:settings-sync-failed', { error: errorReason(error) }))
  return { ok: true, store }
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined
}

function errorReason(error: unknown): string {
  if (error instanceof Error) return error.message
  const reason = String(error)
  return reason.trim() || 'Unknown Web Agent background error'
}

function logBackgroundFailure(event: string, error: unknown, details: Record<string, unknown> = {}): void {
  log.warn(event, { ...details, error: errorReason(error) })
}

function sendResponseSafely(sendResponse: (response?: unknown) => void, response: unknown): void {
  try {
    sendResponse(response)
  } catch (error) {
    logBackgroundFailure('message-response:failed', error)
  }
}

const routeMessage = createMessageRouter([
  { type: 'GROUP_STORE_GET', handler: handleStoreGet },
  ...createChatHandlers({ broadcastStoreUpdated, getChatStatusFromRoles, log, newId, now, runtimeFrames }),
  { type: 'GROUP_SETTINGS_UPDATE', handler: handleSettingsUpdate },
  ...createExternalModelHandlers({ broadcastStoreUpdated, externalModelClient, newId, now }),
  ...createRoleHandlers({ broadcastStoreUpdated, externalModelClient, log, newId, now, runtimeFrames, sendPrompt }),
  ...createOrchestrationHandlers({ broadcastStoreUpdated, broadcastAutoGenerateStream, externalModelClient, getChatStatusFromRoles, log, newId, now, promptDeliveryLimiter, requestRoleRecovery, runtimeFrames, sendPrompt }),
  ...createMessageHandlers({ broadcastStoreUpdated, externalModelClient, getChatStatusFromRoles, log, newId, now, promptDeliveryLimiter, requestRoleRecovery, runtimeFrames, sendError, sendPrompt, sendRoleMessage, siteStatus }),
])

siteStatus.onUnauthorizedToReady(siteId => {
  recoverSiteRolesAfterLogin(siteId).catch(error => log.warn('site-status:login-recovery-scan-failed', { siteId, error: errorReason(error) }))
})

const executeControlCommand = createControlActionExecutor({
  loadStore,
  routeRuntimeMessage(message) {
    return Promise.resolve(routeMessage(message, {}))
  },
  runtimeFrames,
  webAgentPage,
  openDoubaoPage,
  openDeepSeekPage,
  focusSiteTab,
  ensureSiteFrame: ensureSiteFrameForRole,
  waitFor(ms) {
    return new Promise(resolve => globalThis.setTimeout(resolve, ms))
  },
  now,
})

log.info('control-client:create', {
  extensionId: getExtensionId(),
  extensionVersion: getExtensionVersion(),
})

const controlClient = createControlClient({
  loadStore,
  executeCommand: executeControlCommand,
  getExtensionVersion() {
    return getExtensionVersion()
  },
  getProfileId() {
    return getExtensionId()
  },
  log,
  setTimer(handler, ms) {
    return globalThis.setTimeout(handler, ms)
  },
  clearTimer(timerId) {
    globalThis.clearTimeout(timerId)
  },
  onStatusChange(status) {
    return broadcastRuntimeControlStatusUpdated(status)
  },
})
let controlClientInitialized = false

function syncControlClient(): Promise<void> {
  log.info('control-client:sync')
  return controlClient.sync()
}

function initializeControlClient(): void {
  if (controlClientInitialized) return
  controlClientInitialized = true
  try {
    chrome.alarms?.create?.(CONTROL_KEEPALIVE_ALARM, { periodInMinutes: CONTROL_KEEPALIVE_PERIOD_MINUTES })
    log.info('control-client:keepalive-alarm-scheduled', {
      alarm: CONTROL_KEEPALIVE_ALARM,
      periodInMinutes: CONTROL_KEEPALIVE_PERIOD_MINUTES,
    })
  } catch (error) {
    logBackgroundFailure('control-client:keepalive-alarm-failed', error)
  }
  log.info('control-client:initial-sync-scheduled')
  syncControlClient().catch(error => {
    log.warn('control-client:initial-sync-failed', { error: errorReason(error) })
  })
}

function getExtensionVersion(): string {
  try {
    return chrome.runtime.getManifest?.().version ?? 'unknown'
  } catch {
    return 'unknown'
  }
}

function getExtensionId(): string {
  return chrome.runtime.id ?? 'unknown-extension'
}

async function webAgentPage(): Promise<void> {
  await chrome.tabs.create({ url: chrome.runtime.getURL('team.html'), active: true })
}

async function openDoubaoPage(): Promise<void> {
  await chrome.tabs.create({ url: 'https://www.doubao.com/chat/', active: true })
  await new Promise(r => setTimeout(r, 3000))
}

async function openDeepSeekPage(): Promise<void> {
  await chrome.tabs.create({ url: 'https://chat.deepseek.com/', active: true })
  await new Promise(r => setTimeout(r, 3000))
}

async function focusSiteTab(urlPrefix: string, fallbackUrl: string): Promise<{ tabId?: number; created: boolean }> {
  const tabs = await chrome.tabs.query({})
  const open = tabs.find(tab => typeof tab.url === 'string' && tab.url.startsWith(urlPrefix))
  if (open?.id) {
    await chrome.tabs.update(open.id, { active: true })
    return { tabId: open.id, created: false }
  }
  const tab = await chrome.tabs.create({ url: fallbackUrl, active: true })
  return { tabId: tab.id, created: true }
}

const siteFrameBroker = createSiteFrameBroker({
  focusSiteTab,
  sendToTab(tabId, message) {
    return chrome.tabs.sendMessage(tabId, message)
  },
  waitFor(ms) {
    return new Promise(resolve => globalThis.setTimeout(resolve, ms))
  },
  now,
  log(event, details) {
    log.debug(event, details)
  },
})

async function ensureSiteFrameForRole(input: { chatId: string; roleId: string; chatSite?: ChatSite }): Promise<void> {
  const store = await loadStore()
  const role = store.rolesById[input.roleId]
  const site = role?.chatSite ?? input.chatSite
  const startUrl = getDefaultChatSiteUrlForRole(role ?? {}, site)
  const origin = getSupportedChatOriginForSite(role?.geminiConversationUrl, site)
  await siteFrameBroker.ensureSiteFrame({ ...input, chatSite: site, origin, startUrl })
}

chrome.runtime.onInstalled.addListener(() => {
  try {
    log.info('extension-installed')
    initializeControlClient()
  } catch (error) {
    logBackgroundFailure('extension-installed:failed', error)
  }
})

chrome.runtime.onStartup?.addListener(() => {
  initializeControlClient()
})

chrome.alarms?.onAlarm?.addListener(alarm => {
  if (alarm.name !== CONTROL_KEEPALIVE_ALARM) return
  syncControlClient().catch(error => log.warn('control-client:keepalive-sync-failed', { error: errorReason(error) }))
})

chrome.runtime.onMessage.addListener((message: RuntimeMessage, sender, sendResponse) => {
  try {
    if (message?.type === 'WEB_AGENT_PING') {
      sendResponseSafely(sendResponse, { ok: true, tabId: sender.tab?.id ?? null })
      return true
    }

    if (message?.type === 'WEB_AGENT_WORKBENCH_NAVIGATE') {
      const target = message.target
      if (target === 'team') {
        webAgentPage().then(() => sendResponseSafely(sendResponse, { ok: true })).catch((error: unknown) => {
          sendResponseSafely(sendResponse, { ok: false, error: errorReason(error) })
        })
      } else if (target === 'doubao') {
        openDoubaoPage().then(() => sendResponseSafely(sendResponse, { ok: true })).catch((error: unknown) => {
          sendResponseSafely(sendResponse, { ok: false, error: errorReason(error) })
        })
      } else if (target === 'deepseek') {
        openDeepSeekPage().then(() => sendResponseSafely(sendResponse, { ok: true })).catch((error: unknown) => {
          sendResponseSafely(sendResponse, { ok: false, error: errorReason(error) })
        })
      } else if (target === 'focus-deepseek') {
        focusSiteTab('https://chat.deepseek.com/', 'https://chat.deepseek.com/').then(result => sendResponseSafely(sendResponse, { ok: true, ...result })).catch((error: unknown) => {
          sendResponseSafely(sendResponse, { ok: false, error: errorReason(error) })
        })
      } else if (target === 'focus-doubao') {
        focusSiteTab('https://www.doubao.com/', 'https://www.doubao.com/chat/').then(result => sendResponseSafely(sendResponse, { ok: true, ...result })).catch((error: unknown) => {
          sendResponseSafely(sendResponse, { ok: false, error: errorReason(error) })
        })
      } else {
        sendResponseSafely(sendResponse, { ok: false, error: `未知的导航目标：${String(target)}` })
      }
      return true
    }

    Promise.resolve()
      .then(() => routeMessage(message, sender))
      .then(response => sendResponseSafely(sendResponse, response))
      .catch((error: unknown) => {
        const reason = errorReason(error)
        log.warn('message-handler:failed', { type: message?.type, error: reason })
        sendError(reason).catch(() => undefined)
        sendResponseSafely(sendResponse, { ok: false, error: reason })
      })
  } catch (error) {
    const reason = errorReason(error)
    logBackgroundFailure('message-listener:failed', error, { type: message?.type })
    sendError(reason).catch(() => undefined)
    sendResponseSafely(sendResponse, { ok: false, error: reason })
  }

  return true
})

chrome.action.onClicked.addListener(() => {
  try {
    chrome.tabs.create({ url: chrome.runtime.getURL('team.html'), active: true }).catch(error => {
      logBackgroundFailure('open-team-page:failed', error)
    })
  } catch (error) {
    logBackgroundFailure('open-team-page:failed', error)
  }
})

chrome.tabs.onRemoved.addListener(tabId => {
  try {
    forgetHostTab(tabId)
    const removed = runtimeFrames.removeTab(tabId)
    if (removed.length === 0) return

    mutateStore(store => {
      const timestamp = now()
      for (const binding of removed) {
        const role = store.rolesById[binding.roleId]
        if (!role || role.chatId !== binding.chatId || role.status === 'thinking') continue
        role.status = 'loading'
        role.updatedAt = timestamp
      }
    })
      .then(({ store }) => broadcastStoreUpdated(store))
      .catch(error => logBackgroundFailure('tab-removed:update-failed', error, { tabId }))
  } catch (error) {
    logBackgroundFailure('tab-removed:failed', error, { tabId })
  }
})

initializeControlClient()
