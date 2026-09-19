import { chromium } from 'playwright-core'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const BROWSER_LOGS = []

const site = process.env.PROBE_SITE || 'doubao'
const url = process.env.PROBE_URL || (site === 'deepseek' ? 'https://www.deepseek.com/' : 'https://www.doubao.com/chat/')
const bundle = path.join(root, 'dist-probe', 'probe.js')
const resultFile = path.join(root, `result-${site}.json`)

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--disable-blink-features=AutomationControlled'],
})

const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
page.on('console', msg => BROWSER_LOGS.push(`[${msg.type()}] ${msg.text()}`))
page.on('pageerror', err => BROWSER_LOGS.push(`[pageerror] ${err.message}`))

await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
await page.waitForTimeout(4000)

await page.addScriptTag({ path: bundle })

// Start the diagnose inside the page; it writes progress into
// sessionStorage so it survives same-origin navigations.
await page.evaluate(() => {
  window.__webAgentDiagnose().then(
    res => (window.__webAgentProbeResult = { ok: true, result: res }),
    err => (window.__webAgentProbeResult = { ok: false, error: String(err && err.message || err) }),
  )
})

// Poll for result, up to 60s. Read from sessionStorage as fallback (the page
// may navigate and drop in-memory window flags).
let raw = null
for (let i = 0; i < 60; i++) {
  await page.waitForTimeout(1000)
  try {
    raw = await page.evaluate(() => {
      if (window.__webAgentProbeResult) return window.__webAgentProbeResult
      const stored = sessionStorage.getItem('webAgentProbeResult')
      return stored ? JSON.parse(stored) : null
    })
  } catch {
    raw = null
  }
  if (raw) break
}

const result = raw && raw.ok ? raw.result : { failed: true, raw }

result.browserLogs = BROWSER_LOGS.slice(-80)
await page.screenshot({ path: path.join(root, `screenshot-${site}.png`), fullPage: false })
await browser.close()

console.log('PROBE_RESULT_START')
console.log(JSON.stringify(result, null, 2))
console.log('PROBE_RESULT_END')