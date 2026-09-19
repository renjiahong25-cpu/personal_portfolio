#!/usr/bin/env node
import { createHash, randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'node:http'
import { createModelGateway, toStreamChunks } from './model-gateway.mjs'

export const DEFAULT_PORT = 19305
export const DEFAULT_TOKEN_PATH = resolve(homedir(), '.web-agent/control-token')
const DEFAULT_COMMAND_TIMEOUT_MS = 300_000
const MAX_BODY_BYTES = 10 * 1024 * 1024

export async function createControlDaemon(options = {}) {
  const port = Number.isInteger(options.port) ? options.port : DEFAULT_PORT
  const host = options.host ?? '127.0.0.1'
  const token = options.token ?? readOrCreateToken(options.tokenPath ?? DEFAULT_TOKEN_PATH)
  const logToConsole = options.logToConsole ?? true
  const startedAt = Date.now()
  const logs = []
  const pending = new Map()
  let extension

  const gateway = createModelGateway({
    forwardCommand: command => forwardCommand(command),
    log,
  })

  function log(event, details = {}) {
    const entry = { createdAt: Date.now(), event, details }
    logs.push(entry)
    if (logs.length > 300) logs.shift()
    if (logToConsole) console.error(`[web-agent-daemon] ${event}`, details)
  }

  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? '/', `http://${host}`)
      if (request.method === 'GET' && url.pathname === '/ping') {
        writeJson(response, 200, { ok: true })
        return
      }
      if (request.method === 'GET' && url.pathname === '/status') {
        writeJson(response, 200, status())
        return
      }
      if (url.pathname === '/logs') {
        if (!isAuthorized(request, token)) {
          writeJson(response, 401, permissionDenied())
          return
        }
        writeJson(response, 200, { ok: true, logs })
        return
      }
      if (request.method === 'POST' && url.pathname === '/shutdown') {
        if (!isAuthorized(request, token)) {
          writeJson(response, 401, permissionDenied())
          return
        }
        writeJson(response, 200, { ok: true })
        setTimeout(() => daemon.close(), 0)
        return
      }
      if (request.method === 'POST' && url.pathname === '/command') {
        if (!isAuthorized(request, token)) {
          writeJson(response, 401, permissionDenied())
          return
        }
        const command = normalizeCommand(await readJsonBody(request))
        const result = await forwardCommand(command)
        writeJson(response, result.ok ? 200 : statusForError(result.error?.code), result)
        return
      }
      if (request.method === 'GET' && url.pathname === '/v1/models') {
        if (!isAuthorizedOpenAi(request, token)) {
          writeOpenAiError(response, 401, 'permission_denied', '缺少有效的网关鉴权令牌。')
          return
        }
        writeJson(response, 200, { object: 'list', data: gateway.listModels().map(model => ({
          id: model.id,
          object: 'model',
          owned_by: model.owned_by,
        })) })
        return
      }
      if (request.method === 'POST' && url.pathname === '/v1/chat/completions') {
        if (!isAuthorizedOpenAi(request, token)) {
          writeOpenAiError(response, 401, 'permission_denied', '缺少有效的网关鉴权令牌。')
          return
        }
        const body = await readJsonBody(request)
        const wantsStream = body?.stream === true
        const result = await gateway.complete(body)
        if (result?.ok === false && result.statusCode) {
          writeOpenAiError(response, result.statusCode, result.payload?.error?.code, result.payload?.error?.message, result.payload?.error?.hint)
          return
        }
        const payload = responseWithoutFlags(result)
        if (wantsStream) {
          writeOpenAiStream(response, payload)
          return
        }
        writeJson(response, 200, payload)
        return
      }
      writeJson(response, 404, { ok: false, error: { code: 'not_found', message: '接口不存在。' } })
    } catch (error) {
      writeJson(response, 500, {
        ok: false,
        error: {
          code: 'internal_error',
          message: error instanceof Error ? error.message : String(error),
        },
      })
    }
  })

  server.on('upgrade', (request, socket) => {
    try {
      const url = new URL(request.url ?? '/', `http://${host}`)
      if (url.pathname !== '/ext') {
        socket.destroy()
        return
      }
      const key = request.headers['sec-websocket-key']
      if (typeof key !== 'string') {
        socket.destroy()
        return
      }
      const accept = createHash('sha1')
        .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
        .digest('base64')
      socket.write([
        'HTTP/1.1 101 Switching Protocols',
        'Upgrade: websocket',
        'Connection: Upgrade',
        `Sec-WebSocket-Accept: ${accept}`,
        '',
        '',
      ].join('\r\n'))

      extension?.socket.destroy()
      extension = createExtensionConnection(socket, url.searchParams.get('profileId') || 'default')
      log('extension:connected', { profileId: extension.profileId })
    } catch {
      socket.destroy()
    }
  })

  const daemon = {
    get port() {
      const address = server.address()
      return typeof address === 'object' && address ? address.port : port
    },
    status,
    close,
  }

  await new Promise((resolveListen, rejectListen) => {
    server.once('error', rejectListen)
    server.listen(port, host, () => {
      server.off('error', rejectListen)
      resolveListen(undefined)
    })
  })

  function status() {
    return {
      ok: true,
      pid: process.pid,
      uptime: Date.now() - startedAt,
      daemonVersion: '0.1.0',
      extensionConnected: Boolean(extension?.open),
      extensionVersion: extension?.extensionVersion,
      protocolVersion: extension?.protocolVersion,
      profiles: extension?.open ? [{
        profileId: extension.profileId,
        connected: true,
        lastSeenAt: extension.lastSeenAt,
        capabilities: extension.capabilities,
      }] : [],
      pending: pending.size,
      port: daemon.port,
    }
  }

  async function forwardCommand(command) {
    if (!extension?.open) {
      return failure(command.id, 'extension_not_connected', 'Web Agent 扩展尚未连接到本地守护进程。', '请打开已启用 Web Agent 扩展的 Chrome，并在 Web Agent 设置里开启本机智能体控制。')
    }
    return new Promise(resolveCommand => {
      const timeout = setTimeout(() => {
        pending.delete(command.id)
        resolveCommand(failure(command.id, 'task_timeout', '等待 Web Agent 扩展响应超时。'))
      }, command.timeoutMs ?? DEFAULT_COMMAND_TIMEOUT_MS)
      pending.set(command.id, { command, resolve: resolveCommand, timeout })
      sendFrame(extension.socket, JSON.stringify({ type: 'command', command }))
    })
  }

  function createExtensionConnection(socket, profileId) {
    const connection = {
      socket,
      profileId,
      open: true,
      lastSeenAt: Date.now(),
      extensionVersion: undefined,
      protocolVersion: undefined,
      capabilities: [],
      buffer: Buffer.alloc(0),
      textFragments: [],
    }

    socket.on('data', chunk => {
      try {
        connection.buffer = Buffer.concat([connection.buffer, chunk])
        connection.buffer = readFrames(connection.buffer, frame => {
          if (frame.opcode === 8) {
            socket.end()
            return
          }
          if (frame.opcode === 9) {
            sendFrame(socket, frame.payload, 10)
            return
          }
          const text = readTextMessage(connection, frame)
          if (text === undefined) return
          handleExtensionMessage(connection, text)
        })
      } catch (error) {
        log('extension:message-error', { profileId: connection.profileId, error: error instanceof Error ? error.message : String(error) })
        socket.destroy()
      }
    })
    socket.on('close', () => {
      connection.open = false
      if (extension === connection) extension = undefined
      log('extension:closed', { profileId })
    })
    socket.on('error', error => {
      connection.open = false
      log('extension:error', { profileId, error: error.message })
    })
    return connection
  }

  function readTextMessage(connection, frame) {
    if (frame.opcode === 1) {
      if (frame.fin) return frame.payload.toString('utf8')
      connection.textFragments = [frame.payload]
      return undefined
    }
    if (frame.opcode !== 0) return undefined
    if (connection.textFragments.length === 0) return undefined
    connection.textFragments.push(frame.payload)
    if (!frame.fin) return undefined
    const payload = Buffer.concat(connection.textFragments)
    connection.textFragments = []
    return payload.toString('utf8')
  }

  async function handleExtensionMessage(connection, raw) {
    connection.lastSeenAt = Date.now()
    const message = JSON.parse(raw)
    if (message.type === 'hello') {
      connection.extensionVersion = message.extensionVersion
      connection.protocolVersion = message.protocolVersion
      connection.profileId = message.profileId || connection.profileId
      connection.capabilities = Array.isArray(message.capabilities) ? message.capabilities : []
      log('extension:hello', { profileId: connection.profileId, capabilities: connection.capabilities })
      return
    }
    if (message.type === 'result' && message.result?.id) {
      const entry = pending.get(message.result.id)
      if (!entry) return
      clearTimeout(entry.timeout)
      pending.delete(message.result.id)
      const result = message.result
      if (result.ok) {
        const options = entry.command?.payload?.options
        if (options?.downloadDir) {
          try {
            const saved = await downloadImagesFromResult(result, options, log)
            if (saved.length > 0) log('downloads-complete', { count: saved.length, dir: options.downloadDir })
          } catch (error) {
            log('download-error', { error: error instanceof Error ? error.message : String(error) })
          }
        }
      }
      entry.resolve(result)
    }
  }

  async function close() {
    for (const entry of pending.values()) clearTimeout(entry.timeout)
    pending.clear()
    extension?.socket.destroy()
    await new Promise(resolveClose => server.close(() => resolveClose(undefined)))
  }

  return daemon
}

const MARKDOWN_IMAGE_RE = /!\[.*?\]\((https?:\/\/[^\s)]+)\)/g

export async function downloadImagesFromResult(result, options, logFn) {
  const { downloadDir, namePrefix } = options
  mkdirSync(downloadDir, { recursive: true })
  const urls = extractImageUrls(result)
  if (urls.length === 0) return []
  const saved = []
  for (let index = 0; index < urls.length; index++) {
    try {
      const url = urls[index]
      const response = await fetch(url, { signal: AbortSignal.timeout(30000) })
      if (!response.ok) {
        logFn('download-skip', { url, status: response.status })
        continue
      }
      const buffer = Buffer.from(await response.arrayBuffer())
      const ext = getImageExtension(url, response.headers.get('content-type'))
      const label = namePrefix || 'image'
      const suffix = urls.length > 1 ? `_${index + 1}` : ''
      const name = `${label}${suffix}${ext}`
      const filePath = resolve(downloadDir, name)
      writeFileSync(filePath, buffer)
      saved.push({ index, url, filePath, bytes: buffer.length })
      logFn('download-saved', { filePath, bytes: buffer.length })
    } catch (error) {
      logFn('download-skip', { url, error: error instanceof Error ? error.message : String(error) })
    }
  }
  return saved
}

export function extractImageUrls(result) {
  const urls = []
  const replies = result.data?.replies
  if (!Array.isArray(replies)) return urls
  for (const reply of replies) {
    if (typeof reply.content === 'string') {
      let match
      MARKDOWN_IMAGE_RE.lastIndex = 0
      while ((match = MARKDOWN_IMAGE_RE.exec(reply.content)) !== null) {
        urls.push(match[1])
      }
    }
  }
  return urls
}

export function getImageExtension(url, contentType) {
  try {
    const pathname = new URL(url).pathname
    const match = pathname.match(/\.(\w+)(?:[?#]|$)/)
    if (match) return `.${match[1].toLowerCase()}`
  } catch {}
  if (contentType) {
    const map = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/avif': '.avif', 'image/gif': '.gif' }
    for (const [key, ext] of Object.entries(map)) {
      if (contentType.includes(key)) return ext
    }
  }
  return '.png'
}

export function readOrCreateToken(tokenPath = DEFAULT_TOKEN_PATH) {
  if (existsSync(tokenPath)) {
    const token = readFileSync(tokenPath, 'utf8').trim()
    if (token) return token
  }
  mkdirSync(dirname(tokenPath), { recursive: true })
  const token = randomBytes(32).toString('base64url')
  writeFileSync(tokenPath, `${token}\n`, { mode: 0o600 })
  return token
}

function normalizeCommand(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('控制命令必须是 JSON 对象。')
  const id = typeof raw.id === 'string' && raw.id.trim() ? raw.id.trim() : `cmd-${Date.now()}`
  const action = typeof raw.action === 'string' && raw.action.trim() ? raw.action.trim() : ''
  if (!action) throw new Error('缺少控制命令 action。')
  return {
    id,
    action,
    payload: raw.payload,
    timeoutMs: typeof raw.timeoutMs === 'number' ? raw.timeoutMs : undefined,
    profileId: typeof raw.profileId === 'string' ? raw.profileId : undefined,
  }
}

function isAuthorized(request, token) {
  if (request.headers['x-web-agent'] !== '1') return false
  const authorization = request.headers.authorization
  return authorization === `Bearer ${token}`
}

function isAuthorizedOpenAi(request, token) {
  const authorization = request.headers.authorization
  return authorization === `Bearer ${token}`
}

function writeOpenAiError(response, statusCode, code, message, hint) {
  const error = { message: message ?? '请求失败。', type: code ?? 'error' }
  if (hint) error.hint = hint
  error.code = code ?? 'error'
  writeJson(response, statusCode, { error })
}

function writeOpenAiStream(response, payload) {
  response.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
  })
  for (const chunk of toStreamChunks(payload)) {
    response.write(`data: ${JSON.stringify(chunk)}\n\n`)
  }
  response.write('data: [DONE]\n\n')
  response.end()
}

function responseWithoutFlags(result) {
  const { ok, statusCode, ...rest } = result ?? {}
  return rest
}

function permissionDenied() {
  return {
    ok: false,
    error: {
      code: 'permission_denied',
      message: '没有权限访问 Web Agent 本地控制接口。',
      recoverable: false,
    },
  }
}

function failure(id, code, message, hint) {
  return {
    id,
    ok: false,
    error: {
      code,
      message,
      ...(hint ? { hint } : {}),
      recoverable: code !== 'permission_denied',
    },
  }
}

function statusForError(code) {
  if (code === 'permission_denied') return 401
  if (code === 'extension_not_connected') return 503
  if (code === 'task_timeout') return 504
  return 400
}

async function readJsonBody(request) {
  const chunks = []
  let total = 0
  for await (const chunk of request) {
    total += chunk.length
    if (total > MAX_BODY_BYTES) throw new Error('请求体超过 1 MB。')
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

function writeJson(response, statusCode, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  })
  response.end(body)
}

function readFrames(buffer, onFrame) {
  let offset = 0
  while (buffer.length - offset >= 2) {
    const first = buffer[offset]
    const second = buffer[offset + 1]
    const fin = Boolean(first & 0x80)
    const opcode = first & 0x0f
    const masked = Boolean(second & 0x80)
    let length = second & 0x7f
    let headerLength = 2
    if (length === 126) {
      if (buffer.length - offset < 4) break
      length = buffer.readUInt16BE(offset + 2)
      headerLength = 4
    } else if (length === 127) {
      if (buffer.length - offset < 10) break
      const bigLength = buffer.readBigUInt64BE(offset + 2)
      if (bigLength > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('WebSocket frame too large')
      length = Number(bigLength)
      headerLength = 10
    }
    const maskLength = masked ? 4 : 0
    const frameLength = headerLength + maskLength + length
    if (buffer.length - offset < frameLength) break
    const mask = masked ? buffer.subarray(offset + headerLength, offset + headerLength + 4) : undefined
    const payloadStart = offset + headerLength + maskLength
    const payload = Buffer.from(buffer.subarray(payloadStart, payloadStart + length))
    if (mask) {
      for (let index = 0; index < payload.length; index += 1) payload[index] ^= mask[index % 4]
    }
    onFrame({ fin, opcode, payload })
    offset += frameLength
  }
  return buffer.subarray(offset)
}

function sendFrame(socket, data, opcode = 1) {
  const payload = Buffer.isBuffer(data) ? data : Buffer.from(String(data))
  let header
  if (payload.length < 126) {
    header = Buffer.from([0x80 | opcode, payload.length])
  } else if (payload.length < 65536) {
    header = Buffer.alloc(4)
    header[0] = 0x80 | opcode
    header[1] = 126
    header.writeUInt16BE(payload.length, 2)
  } else {
    header = Buffer.alloc(10)
    header[0] = 0x80 | opcode
    header[1] = 127
    header.writeBigUInt64BE(BigInt(payload.length), 2)
  }
  socket.write(Buffer.concat([header, payload]))
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])
if (isMain) {
  const portArg = process.argv.find(arg => arg.startsWith('--port='))
  const port = portArg ? Number(portArg.slice('--port='.length)) : DEFAULT_PORT
  createControlDaemon({ port })
    .then(daemon => {
      console.error(`[web-agent-daemon] listening on 127.0.0.1:${daemon.port}`)
    })
    .catch(error => {
      console.error(error instanceof Error ? error.message : String(error))
      process.exitCode = 1
    })
}
