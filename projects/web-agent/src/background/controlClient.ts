import {
  WEB_AGENT_CONTROL_CAPABILITIES,
  WEB_AGENT_CONTROL_DEFAULT_PORT,
  WEB_AGENT_CONTROL_PROTOCOL_VERSION,
  controlFailure,
  isRecord,
  type ControlHttpCommand,
  type ControlHttpResult,
  type DaemonControlMessage,
  type ExtensionControlMessage,
  type WebAgentControlConnectionStatus,
} from '../shared/localControlProtocol'
import type { WebAgentStore } from '../group/types'

export interface ControlClientDependencies {
  loadStore(): Promise<WebAgentStore>
  executeCommand(command: ControlHttpCommand): Promise<ControlHttpResult>
  getExtensionVersion(): string
  getProfileId(): string
  log: {
    debug(event: string, details?: Record<string, unknown>): void
    info(event: string, details?: Record<string, unknown>): void
    warn(event: string, details?: Record<string, unknown>): void
  }
  setTimer(handler: () => void, ms: number): ReturnType<typeof globalThis.setTimeout>
  clearTimer(timerId: ReturnType<typeof globalThis.setTimeout>): void
  onStatusChange?(status: WebAgentControlConnectionStatus): void | Promise<void>
}

export interface ControlClient {
  sync(): Promise<void>
  stop(): void
  status(): WebAgentControlConnectionStatus
}

const DAEMON_PING_TIMEOUT_MS = 1_000
const RECONNECT_BASE_DELAY_MS = 2_000
const RECONNECT_MAX_DELAY_MS = 60_000

export function createControlClient(deps: ControlClientDependencies): ControlClient {
  let socket: WebSocket | undefined
  let activeUrl: string | undefined
  let connectInFlight: { url: string; promise: Promise<void> } | undefined
  let reconnectTimer: ReturnType<typeof globalThis.setTimeout> | undefined
  let reconnectAttempts = 0
  let shouldReconnect = false
  let currentStatus: WebAgentControlConnectionStatus = {
    state: 'disabled',
    port: WEB_AGENT_CONTROL_DEFAULT_PORT,
  }

  diagnostic('info', 'createControlClient:created', {
    profileId: safeCall(deps.getProfileId),
    extensionVersion: safeCall(deps.getExtensionVersion),
  })

  async function sync(): Promise<void> {
    diagnostic('info', 'sync:start', {
      hasSocket: Boolean(socket),
      activeUrl,
      socketReadyState: socket?.readyState,
      shouldReconnect,
    })
    const store = await deps.loadStore()
    const port = controlDaemonPort(store)
    diagnostic('info', 'sync:store-loaded', {
      agentControlEnabled: store.settings.agentControlEnabled,
      agentControlPort: store.settings.agentControlPort,
      defaultChatSite: store.settings.defaultChatSite,
    })
    if (!store.settings.agentControlEnabled) {
      shouldReconnect = false
      clearReconnect()
      closeSocket()
      updateStatus({ state: 'disabled', port })
      diagnostic('info', 'sync:disabled', {
        reason: 'store.settings.agentControlEnabled is false',
      })
      return
    }

    shouldReconnect = true
    const url = controlDaemonUrl(port, deps.getProfileId())
    updateStatus({ state: 'connecting', port, url })
    diagnostic('info', 'sync:enabled', { url })
    if (socket && activeUrl === url && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
      diagnostic('info', 'sync:reuse-existing-socket', { url, readyState: socket.readyState })
      updateStatus(socket.readyState === WebSocket.OPEN
        ? { state: 'connected', port, url }
        : { state: 'connecting', port, url })
      return
    }
    closeSocket()
    await connect(url)
  }

  function stop(): void {
    diagnostic('warn', 'stop', { activeUrl, readyState: socket?.readyState })
    shouldReconnect = false
    reconnectAttempts = 0
    clearReconnect()
    closeSocket()
    updateStatus({ state: 'disabled', port: currentStatus.port })
  }

  function status(): WebAgentControlConnectionStatus {
    return currentStatus
  }

  function connect(url: string): Promise<void> {
    if (connectInFlight?.url === url) {
      diagnostic('info', 'connect:already-in-flight', { activeUrl })
      return connectInFlight.promise
    }
    const promise = connectAttempt(url).finally(() => {
      if (connectInFlight?.promise === promise) connectInFlight = undefined
    })
    connectInFlight = { url, promise }
    return promise
  }

  async function connectAttempt(url: string): Promise<void> {
    activeUrl = url
    diagnostic('info', 'connect:attempt', { url })
    const daemonReachable = await probeDaemon(url)
    if (!daemonReachable) {
      diagnostic('warn', 'connect:ping-unavailable', { url })
      updateStatus({ state: 'disconnected', port: portFromControlUrl(url), url, lastError: 'Web Agent CLI daemon is not reachable.' })
      scheduleReconnect()
      return
    }
    if (!shouldReconnect || activeUrl !== url) {
      diagnostic('warn', 'connect:aborted-after-ping', { url, activeUrl, shouldReconnect })
      return
    }
    try {
      socket = new WebSocket(url)
    } catch (error) {
      diagnostic('warn', 'connect:constructor-failed', {
        url,
        error: error instanceof Error ? error.message : String(error),
      })
      deps.log.warn('control-client:constructor-failed', { url, error: error instanceof Error ? error.message : String(error) })
      updateStatus({ state: 'disconnected', port: portFromControlUrl(url), url, lastError: error instanceof Error ? error.message : String(error) })
      scheduleReconnect()
      return
    }
    deps.log.info('control-client:connect', { url })
    diagnostic('info', 'connect:socket-created', { url, readyState: socket.readyState })

    socket.onopen = () => {
      deps.log.info('control-client:connected', { url })
      reconnectAttempts = 0
      diagnostic('info', 'socket:open', { url, readyState: socket?.readyState })
      updateStatus({ state: 'connected', port: portFromControlUrl(url), url })
      send({
        type: 'hello',
        extensionVersion: deps.getExtensionVersion(),
        protocolVersion: WEB_AGENT_CONTROL_PROTOCOL_VERSION,
        profileId: deps.getProfileId(),
        capabilities: [...WEB_AGENT_CONTROL_CAPABILITIES],
      })
    }
    socket.onmessage = event => {
      diagnostic('info', 'socket:message', {
        url,
        dataType: typeof event.data,
        dataLength: typeof event.data === 'string' ? event.data.length : undefined,
      })
      handleSocketMessage(event.data).catch(error => {
        diagnostic('warn', 'socket:message-failed', {
          url,
          error: error instanceof Error ? error.message : String(error),
        })
        deps.log.warn('control-client:message-failed', { error: error instanceof Error ? error.message : String(error) })
      })
    }
    socket.onerror = event => {
      diagnostic('warn', 'socket:error', {
        url,
        readyState: socket?.readyState,
        eventType: event.type,
      })
      deps.log.warn('control-client:error', { url })
      updateStatus({ state: 'disconnected', port: portFromControlUrl(url), url, lastError: 'Web Agent CLI daemon connection failed.' })
    }
    socket.onclose = event => {
      diagnostic('warn', 'socket:close', {
        url,
        code: event.code,
        reason: event.reason,
        wasClean: event.wasClean,
        shouldReconnect,
      })
      deps.log.warn('control-client:closed', { url })
      socket = undefined
      if (shouldReconnect) {
        updateStatus({ state: 'disconnected', port: portFromControlUrl(url), url, lastError: 'Web Agent CLI daemon connection closed.' })
        scheduleReconnect()
      }
    }
  }

  async function probeDaemon(url: string): Promise<boolean> {
    const pingUrl = controlDaemonPingUrl(url)
    diagnostic('info', 'connect:ping-start', { pingUrl })
    const controller = new AbortController()
    const timeout = globalThis.setTimeout(() => controller.abort(), DAEMON_PING_TIMEOUT_MS)
    try {
      const response = await fetch(pingUrl, {
        method: 'GET',
        cache: 'no-store',
        signal: controller.signal,
      })
      diagnostic(response.ok ? 'info' : 'warn', 'connect:ping-finished', {
        pingUrl,
        ok: response.ok,
        status: response.status,
      })
      return response.ok
    } catch (error) {
      diagnostic('warn', 'connect:ping-failed', {
        pingUrl,
        error: error instanceof Error ? error.message : String(error),
      })
      return false
    } finally {
      globalThis.clearTimeout(timeout)
    }
  }

  async function handleSocketMessage(data: unknown): Promise<void> {
    const message = parseDaemonMessage(data)
    if (!message) {
      diagnostic('warn', 'daemon-message:ignored', { dataType: typeof data })
      return
    }
    diagnostic('info', 'daemon-message:command', {
      id: message.command.id,
      action: message.command.action,
      timeoutMs: message.command.timeoutMs,
    })
    const result = await deps.executeCommand(message.command)
    diagnostic(result.ok ? 'info' : 'warn', 'daemon-message:result', {
      id: result.id,
      ok: result.ok,
      errorCode: result.error?.code,
    })
    send({ type: 'result', result })
  }

  function scheduleReconnect(): void {
    if (reconnectTimer !== undefined) {
      diagnostic('info', 'reconnect:already-scheduled')
      return
    }
    const delayMs = Math.min(RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempts, RECONNECT_MAX_DELAY_MS)
    reconnectAttempts += 1
    diagnostic('warn', 'reconnect:scheduled', { delayMs, activeUrl, reconnectAttempts })
    reconnectTimer = deps.setTimer(() => {
      reconnectTimer = undefined
      diagnostic('info', 'reconnect:tick', { activeUrl })
      sync().catch(error => deps.log.warn('control-client:reconnect-failed', { error: error instanceof Error ? error.message : String(error) }))
    }, delayMs)
  }

  function clearReconnect(): void {
    if (reconnectTimer === undefined) return
    deps.clearTimer(reconnectTimer)
    reconnectTimer = undefined
    diagnostic('info', 'reconnect:cleared')
  }

  function closeSocket(): void {
    const current = socket
    socket = undefined
    if (!current) return
    diagnostic('warn', 'socket:close-requested', { activeUrl, readyState: current.readyState })
    current.onclose = null
    current.close()
  }

  function send(message: ExtensionControlMessage): void {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      diagnostic('warn', 'socket:send-skipped', {
        messageType: message.type,
        hasSocket: Boolean(socket),
        readyState: socket?.readyState,
      })
      return
    }
    diagnostic('info', 'socket:send', {
      messageType: message.type,
      resultId: message.type === 'result' ? message.result.id : undefined,
      ok: message.type === 'result' ? message.result.ok : undefined,
    })
    socket.send(JSON.stringify(message))
  }

  function updateStatus(nextStatus: WebAgentControlConnectionStatus): void {
    if (sameStatus(currentStatus, nextStatus)) return
    currentStatus = nextStatus
    diagnostic(currentStatus.state === 'disconnected' ? 'warn' : 'info', 'status:update', { ...currentStatus })
    Promise.resolve(deps.onStatusChange?.(currentStatus)).catch(error => {
      deps.log.warn('control-client:status-broadcast-failed', { error: error instanceof Error ? error.message : String(error) })
    })
  }

  return { sync, stop, status }
}

function controlDaemonPort(store: WebAgentStore): number {
  return typeof store.settings.agentControlPort === 'number' && Number.isFinite(store.settings.agentControlPort)
    ? store.settings.agentControlPort
    : WEB_AGENT_CONTROL_DEFAULT_PORT
}

function controlDaemonUrl(port: number, profileId: string): string {
  return `ws://127.0.0.1:${port}/ext?profileId=${encodeURIComponent(profileId)}`
}

function portFromControlUrl(socketUrl: string): number {
  const parsed = Number(new URL(socketUrl).port)
  return Number.isInteger(parsed) ? parsed : WEB_AGENT_CONTROL_DEFAULT_PORT
}

function controlDaemonPingUrl(socketUrl: string): string {
  const url = new URL(socketUrl)
  url.protocol = 'http:'
  url.pathname = '/ping'
  url.search = ''
  return url.toString()
}

function parseDaemonMessage(data: unknown): DaemonControlMessage | undefined {
  const parsed = typeof data === 'string' ? JSON.parse(data) as unknown : data
  if (!isRecord(parsed) || parsed.type !== 'command' || !isRecord(parsed.command)) return undefined
  const id = typeof parsed.command.id === 'string' ? parsed.command.id : ''
  const action = typeof parsed.command.action === 'string' ? parsed.command.action : ''
  if (!id || !action) {
    return {
      type: 'command',
      command: { id: id || 'invalid-command', action: 'invalid' },
    }
  }
  return {
    type: 'command',
    command: {
      id,
      action,
      payload: parsed.command.payload,
      timeoutMs: typeof parsed.command.timeoutMs === 'number' ? parsed.command.timeoutMs : undefined,
      profileId: typeof parsed.command.profileId === 'string' ? parsed.command.profileId : undefined,
    },
  }
}

export function invalidControlCommandResult(id: string): ControlHttpResult {
  return controlFailure(id, 'invalid_request', '控制命令格式无效。', { recoverable: false })
}

function diagnostic(level: 'info' | 'warn', event: string, details: Record<string, unknown> = {}): void {
  const payload = {
    at: new Date().toISOString(),
    ...details,
  }
  console[level](`[Web Agent][control] ${event}`, payload)
}

function safeCall(read: () => string): string {
  try {
    return read()
  } catch (error) {
    return `unavailable:${error instanceof Error ? error.message : String(error)}`
  }
}

function sameStatus(left: WebAgentControlConnectionStatus, right: WebAgentControlConnectionStatus): boolean {
  return left.state === right.state &&
    left.port === right.port &&
    left.url === right.url &&
    left.lastError === right.lastError
}
