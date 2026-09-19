import { spawn, spawnSync } from 'node:child_process'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const argv = process.argv.slice(2)
const timeoutMs = Number(process.env.VITEST_E2E_TIMEOUT_MS ?? 180000)
const LOG_FILE = process.env.VITEST_E2E_LOG ?? path.join(process.cwd(), 'vitest-e2e.log')
const vitestBin = path.join(root, 'node_modules', 'vitest', 'vitest.mjs')

const startedAt = Date.now()
let child = null
let stdout = ''
let stderr = ''
let timedOut = false

function log(...args) {
  console.log('[vitest-e2e]', ...args)
}

function appendLog(line) {
  try {
    appendFileSync(LOG_FILE, line + '\n')
  } catch {}
}

function killTree(pid) {
  try {
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
  } catch {}
  try {
    child?.kill('SIGKILL')
  } catch {}
}

function cleanOrphanVitest() {
  const selfPid = process.pid
  const script = [
    `-NoProfile`,
    `-Command`,
    `Get-CimInstance Win32_Process -Filter "name='node.exe'" | Where-Object { $_.CommandLine -like '*vitest*' -and $_.ProcessId -ne ${selfPid} } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }`,
  ]
  spawnSync('powershell', script, { windowsHide: true, stdio: 'ignore' })
}

function closeChildStreams() {
  try { child?.stdout?.destroy() } catch {}
  try { child?.stderr?.destroy() } catch {}
}

function finish(code) {
  closeChildStreams()
  const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1)
  log(`finish code=${code} timedOut=${timedOut} elapsed=${elapsed}s log=${LOG_FILE}`)
  // Let the event loop drain instead of force-exiting, avoiding libuv
  // "UV_HANDLE_CLOSING" teardown asserts on Windows (0xC0000409).
  setTimeout(() => process.exit(code), 100)
}

function summarize() {
  const text = (stdout + '\n' + stderr).trimEnd()
  const lines = text.split('\n')
  const summary = lines.filter(l =>
    /Test Files|Tests |Duration|FAIL |PASS |✓|×|AssertionError|Error:/.test(l),
  )
  log('--- vitest summary (filtered) ---')
  for (const line of summary.slice(-20)) console.log('  ', line)
  log('--- last 25 raw lines ---')
  for (const line of lines.slice(-25)) console.log('  ', line)
}

async function run() {
  if (!existsSync(vitestBin)) {
    console.error('[vitest-e2e] FAIL — vitest binary not found:', vitestBin)
    finish(3)
    return
  }

  log(`spawn: node ${vitestBin} run ${argv.join(' ') || '(default suite)'} (timeout ${timeoutMs}ms)`)
  log('log file:', LOG_FILE)
  try {
    appendFileSync(LOG_FILE, `\n=== vitest-e2e run ${new Date().toISOString()} args=[${argv.join(' ')}] ===\n`)
  } catch {}

  child = spawn(process.execPath, [vitestBin, 'run', ...argv], {
    cwd: root,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, FORCE_COLOR: '0' },
  })
  log('vitest pid:', child.pid)

  const hardDeadline = setTimeout(() => {
    timedOut = true
    console.error(`[vitest-e2e] GLOBAL TIMEOUT after ${timeoutMs}ms — killing process tree pid=${child?.pid}`)
    appendLog(`GLOBAL TIMEOUT after ${timeoutMs}ms — killing process tree`)
    killTree(child?.pid)
  }, timeoutMs)

  child.stdout.on('data', d => {
    const text = d.toString()
    stdout += text
    appendLog(text)
    process.stdout.write(text)
  })
  child.stderr.on('data', d => {
    const text = d.toString()
    stderr += text
    appendLog(text)
    process.stderr.write(text)
  })

  const exitCode = await new Promise(resolve => {
    child.once('exit', (code, signal) => resolve({ code, signal }))
  })

  clearTimeout(hardDeadline)
  cleanOrphanVitest()

  if (timedOut) {
    console.error('[vitest-e2e] FAIL — run exceeded hard timeout, process tree killed')
    summarize()
    finish(2)
    return
  }

  log(`vitest exit code:`, exitCode.code, 'signal:', exitCode.signal ?? 'none')
  if (exitCode.code === 0) {
    const tail = (stdout + '\n' + stderr).trimEnd().split('\n').slice(-10)
    log('PASS — tail:')
    for (const line of tail) console.log('  ', line)
    finish(0)
    return
  }

  console.error(`[vitest-e2e] FAIL — vitest exited with code ${exitCode.code}`)
  summarize()
  finish(exitCode.code ?? 1)
}

run().catch(error => {
  console.error('[vitest-e2e] unexpected error:', error)
  killTree(child?.pid)
  cleanOrphanVitest()
  closeChildStreams()
  process.exit(1)
})
