import { createDoubaoAdapter } from '../src/content/sites/doubao'
import { createDeepSeekAdapter } from '../src/content/sites/deepseek'
import type { ChatSiteAdapter } from '../src/content/sites/types'

declare global {
  interface Window {
    __webAgentDiagnose: () => Promise<unknown>
  }
}

function adapterFor(site: 'doubao' | 'deepseek'): ChatSiteAdapter {
  if (site === 'deepseek') {
    return createDeepSeekAdapter({ inputTimeoutMs: 6000 })
  }
  return createDoubaoAdapter({ inputTimeoutMs: 6000, skillButtonTimeoutMs: 1500 })
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

window.__webAgentDiagnose = async () => {
  const report = async (doc: unknown) => {
    try { sessionStorage.setItem('webAgentProbeResult', JSON.stringify(doc)) } catch { /* ignore */ }
  }
  const hostname = location.hostname
  const site: 'doubao' | 'deepseek' = hostname.includes('deepseek') ? 'deepseek' : 'doubao'
  const adapter = adapterFor(site)
  const before = adapter.collectPromptDiagnostics()

  // Composers / send candidates BEFORE we type anything.
  const preState = {
    editorElements: [...document.querySelectorAll('textarea, [contenteditable="true"]')]
      .slice(0, 5)
      .map(el => ({ tag: el.tagName, cls: String(el.className).slice(0, 70), ph: el.getAttribute('placeholder'), ce: el.getAttribute('contenteditable'), rect: (() => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } })(), visible: el.getBoundingClientRect().width > 0 })),
    sendButton: {
      flowEndMsgSend: Boolean(document.querySelector('#flow-end-msg-send')),
      textareaSend: [...document.querySelectorAll('button, [role="button"]')].filter(el => (el.getAttribute('aria-label') || '').includes('发送')).slice(0, 3).length,
    },
  }

  let fill: unknown
  try {
    fill = { ok: true }
    await adapter.fillAndSend('你好 Web Agent 有头诊断', true)
  } catch (error) {
    fill = { ok: false, error: error instanceof Error ? error.message : String(error) }
  }

  // Post-state: did the composer show the text? Did a send happen (message area updated)?
  const postState = {
    editorText: (() => {
      const el = document.querySelector<HTMLElement>('div.tiptap.ProseMirror[contenteditable="true"], textarea.hero-input')
      if (!el) return { found: false }
      return el instanceof HTMLTextAreaElement ? { found: true, tag: 'textarea', value: el.value } : { found: true, tag: 'div', text: el.textContent?.slice(0, 200) }
    })(),
    sendButtonNow: {
      flowEndMsgSend: (() => {
        const s = document.querySelector('#flow-end-msg-send')
        return s ? { found: true, ariaDisabled: s.getAttribute('aria-disabled'), disabled: s.hasAttribute('disabled'), html: s.outerHTML.slice(0, 200) } : { found: false }
      })(),
    },
    messageAreaText: document.body.innerText.slice(0, 600),
    lastMessageCount: (() => {
      const blocks = document.querySelectorAll('[data-testid="message-block-container"], [data-render-engine="node"]')
      return blocks.length
    })(),
  }

  await report({ phase: 'afterFill', ...preState, fill })

  await delay(4000)
  const afterWait = {
    editorText: (() => {
      const el = document.querySelector<HTMLElement>('div.tiptap.ProseMirror[contenteditable="true"], textarea.hero-input')
      if (!el) return { found: false }
      return el instanceof HTMLTextAreaElement ? { found: true, tag: 'textarea', value: el.value } : { found: true, tag: 'div', text: el.textContent?.slice(0, 200) }
    })(),
    messageAreaText: document.body.innerText.slice(0, 600),
    lastMessageCount: (() => {
      const blocks = document.querySelectorAll('[data-testid="message-block-container"], [data-render-engine="node"]')
      return blocks.length
    })(),
    lastBlockHtml: (() => {
      const blocks = [...document.querySelectorAll('[data-testid="message-block-container"], [data-render-engine="node"]')]
      const last = blocks[blocks.length - 1]
      return last ? last.outerHTML.replace(/\s+/g, ' ').slice(0, 1200) : undefined
    })(),
  }

  const out = {
    site,
    href: location.href,
    title: document.title,
    preState,
    fill,
    postState,
    afterWait,
    adapterDiagnosticsAfter: adapter.collectPromptDiagnostics(),
  }
  await report(out)
  return out
}

export {}