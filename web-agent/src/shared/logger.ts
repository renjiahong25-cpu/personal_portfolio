export type LogDetails = Record<string, unknown>

export interface WebAgentLogger {
  debug(event: string, details?: LogDetails): void
  info(event: string, details?: LogDetails): void
  warn(event: string, details?: LogDetails): void
  error(event: string, details?: LogDetails): void
  child(context: LogDetails): WebAgentLogger
}

export interface LoggerOptions {
  debugEnabled?: boolean
}

type ConsoleLevel = 'debug' | 'info' | 'warn' | 'error'

declare const __WEB_AGENT_DEV__: boolean | undefined

export function createLogger(scope: string, baseContext: LogDetails = {}, options: LoggerOptions = {}): WebAgentLogger {
  const shouldEmitVerbose = () => options.debugEnabled ?? isDebugLoggingEnabled()

  const emit = (level: ConsoleLevel, event: string, details: LogDetails = {}): void => {
    if (!shouldEmitVerbose()) return

    const payload = { ...baseContext, ...details }
    console[level](`[Web Agent][${scope}] ${event}`, payload)
  }

  return {
    debug: (event, details) => emit('debug', event, details),
    info: (event, details) => emit('info', event, details),
    warn: (event, details) => emit('warn', event, details),
    error: (event, details) => emit('error', event, details),
    child: context => createLogger(scope, { ...baseContext, ...context }, options),
  }
}

function isDebugLoggingEnabled(): boolean {
  if (typeof __WEB_AGENT_DEV__ !== 'undefined' && __WEB_AGENT_DEV__) return true

  try {
    const globalRecord = globalThis as unknown as { WEB_AGENT_DEBUG?: boolean; localStorage?: Storage; location?: Location }
    if (globalRecord.WEB_AGENT_DEBUG === true) return true
    if (globalRecord.localStorage?.getItem('web-agent:debug') === 'true') return true
    if (globalRecord.location?.search.includes('web-agent_debug=1')) return true
  } catch {
    // Debug logging should never affect app behavior.
  }

  return false
}
