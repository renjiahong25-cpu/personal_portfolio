import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = 19305
const BASE = `http://127.0.0.1:${PORT}`
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const tokenPath = path.join(homedir(), '.web-agent', 'control-token')
const daemonFile = path.join(root, 'packages', 'web-agent', 'web-agent-daemon.mjs')
const LOG_OUT = process.env.WEB_AGENT_E2E_LOG ?? 'daemon-e2e-out.log'

let child = null
let step = 0

function log(...args) {
  console.log(`[e2e:${String(step++).padStart(2, '0')}]`, ...args)
}

function token() {
  return readFileSync(tokenPath, 'utf8').trim()
}

async function request(pathname, { method = 'GET', body, auth = true, control = false } = {}) {
  const headers = {}
  if (control) headers['x-web-agent'] = '1'
  if (body) headers['content-type'] = 'application/json'
  if (auth) headers.authorization = `Bearer ${token()}`
  const response = await fetch(`${BASE}${pathname}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(5000),
  })
  const text = await response.text()
  let json = null
  try {
    json = text ? JSON.parse(text) : null
  } catch {}
  return { status: response.status, json, text }
}

async function waitForReady(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await request('/status', { auth: false })
      if (r.status === 200) return true
    } catch {}
    await new Promise(r => setTimeout(r, 300))
  }
  return false
}

async function shutdownExisting() {
  try {
    const r = await request('/shutdown', { method: 'POST', body: {}, auth: true, control: true })
    log('cleanup existing daemon on port:', r.status)
  } catch {
    log('no existing daemon on port:', PORT)
  }
}

async function startDaemon() {
  child = spawn(process.execPath, [daemonFile], {
    cwd: root,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, WEB_AGENT_E2E_LOG: undefined },
  })
  let stdout = ''
  let stderr = ''
  child.stdout.on('data', d => (stdout += d))
  child.stderr.on('data', d => (stderr += d))
  return { stdout, stderr }
}

function forceKill() {
  if (child && !child.killed) {
    try {
      child.kill('SIGKILL')
    } catch {}
  }
}

function closeChildStreams() {
  if (child) {
    try { child.stdout?.destroy() } catch {}
    try { child.stderr?.destroy() } catch {}
  }
}

function finish(code) {
  closeChildStreams()
  log('e2e exit code:', code)
  // Let the event loop drain instead of force-exiting, avoiding libuv
  // "UV_HANDLE_CLOSING" teardown asserts on Windows (0xC0000409).
  setTimeout(() => process.exit(code), 50)
}

async function run() {
  log('e2e: Web Agent daemon lifecycle probe (auto-stops)')
  const hardDeadline = setTimeout(() => {
    console.error('[e2e] GLOBAL TIMEOUT — force-killing daemon')
    forceKill()
    finish(2)
  }, 60000)

  const probe = await startDaemon()
  log('daemon spawned, pid:', child.pid)

  const ready = await waitForReady()
  if (!ready) {
    console.error('[e2e] FAIL — daemon did not become ready')
    console.error('stderr tail:', probe.stderr.slice(-800))
    forceKill()
    finish(1)
    return
  }
  log('PASS — daemon ready (GET /status 200 without auth)')

  const checks = []

  let r = await request('/status')
  checks.push(['GET /status authed 200', r.status === 200 && r.json?.ok === true, r])
  if (r.json) log('  status fields:', JSON.stringify(r.json))

  r = await request('/v1/models')
  checks.push(['GET /v1/models authed 200', r.status === 200 && Array.isArray(r.json?.data), r])

  r = await request('/v1/models', { auth: false })
  checks.push(['GET /v1/models no-auth 401', r.status === 401, r])

  r = await request('/v1/chat/completions', { method: 'POST', body: {}, auth: false })
  checks.push(['POST /v1/chat/completions no-auth 401', r.status === 401, r])

  r = await request('/v1/chat/completions', {
    method: 'POST',
    auth: true,
    body: {
      model: 'deepseek',
      messages: [{ role: 'user', content: '1+1=? (control-plane probe, do not forward)' }],
    },
  })
  const hasOpenAiErrorShape = r.json?.error && (r.json.error.code || r.json.error.message)
  checks.push(
    ['POST /v1/chat/completions authed returns OpenAI error shape (no extension connected)',
     r.status >= 400 && hasOpenAiErrorShape, r],
  )
  if (r.json?.error) log('  gateway error:', r.json.error.code, '—', r.json.error.message)

  r = await request('/shutdown', { method: 'POST', body: {}, auth: true, control: true })
  checks.push(['POST /shutdown authed 200', r.status === 200, r])

  const exitCode = await new Promise(resolve => {
    const t = setTimeout(() => resolve('TIMEOUT'), 5000)
    child.once('exit', code => {
      clearTimeout(t)
      resolve(code)
    })
  })
  checks.push(['daemon process exited after /shutdown', exitCode !== 'TIMEOUT', { exitCode }])
  log('daemon exit code:', exitCode)

  clearTimeout(hardDeadline)
  const failed = checks.filter(([, ok]) => !ok)
  log(failed.length === 0 ? 'ALL PASS' : `${failed.length} FAIL`)
  for (const [name, ok, detail] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : ` -> ${JSON.stringify(detail)}`}`)
  }
  log('daemon stdout tail:', probe.stdout.slice(-400))
  finish(failed.length === 0 ? 0 : 1)
}

run().catch(error => {
  console.error('[e2e] unexpected error:', error)
  forceKill()
  closeChildStreams()
  process.exit(1)
})