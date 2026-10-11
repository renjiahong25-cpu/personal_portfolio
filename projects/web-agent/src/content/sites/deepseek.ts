import type { ChatSiteAdapter, ConversationSnapshot, SiteStatusInfo } from './types'
import { keepDeepestResponseContainers } from '../responseContainers'
import { extractMarkdownFromDom } from './domMarkdown'
import { buttonLabelMatches, describeElement, extractCleanTextFromDom, findClosestMatchingAncestor } from './domText'
import { isClickableButton, waitForElement } from './waitForElement'

const DEEPSEEK_HOSTS = ['chat.deepseek.com', 'www.deepseek.com', 'deepseek.com']
const DEFAULT_INPUT_TIMEOUT_MS = 9000
const DEEPSEEK_LOGIN_REQUIRED_ERROR = 'SITE_LOGIN_REQUIRED'
const DEEPSEEK_LOGIN_PATH_PATTERN = /^\/(auth|login|signin|sign-up|signup|register|entry|account)\b/i

const DEEPSEEK_SELECTORS = {
  editor:
    'textarea.hero-input, textarea[placeholder*="问点什么"], textarea[placeholder*="探索"], ' +
    'textarea[name="search"], textarea[placeholder*="DeepSeek"], textarea[placeholder*="发送消息"]',
  response: '[data-virtual-list-item-key] .ds-message .ds-markdown:not(.ds-think-content .ds-markdown)',
  responseContainer: '[data-virtual-list-item-key]',
  composer: '[class*="ds-input-wrapper"], [class*="composer"], .aaff8b8f, ._77cefa5',
  sendButton:
    'button[aria-label*="发送"], button[aria-label*="send" i], [role="button"][aria-label*="发送"], ' +
    '[role="button"][aria-label*="send" i], button[data-testid*="send" i], [role="button"][data-testid*="send" i], ' +
    '.bf38813a [role="button"], .bf38813a button, [role="button"]._52c986b, button._52c986b',
}

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'BUTTON', 'TEXTAREA', 'SVG'])

interface DeepSeekAdapterOptions {
  href?: string
  inputTimeoutMs?: number
}

export function createDeepSeekAdapter(options: DeepSeekAdapterOptions = {}): ChatSiteAdapter {
  const inputTimeoutMs = options.inputTimeoutMs ?? DEFAULT_INPUT_TIMEOUT_MS

  function currentHref(): string {
    return options.href ?? location.href
  }

  function getConversationSnapshot(): ConversationSnapshot {
    return getDeepSeekConversationLocation(currentHref())
  }

  function getConversationId(): string {
    return getConversationSnapshot().conversationId || '__default__'
  }

  function checkDeepSeekStatus(): SiteStatusInfo {
    const timestamp = Date.now()
    let href = ''
    try {
      href = currentHref()
    } catch {
      href = ''
    }

    if (isDeepSeekLoginPath(href)) {
      return { status: 'unauthorized', detail: 'DeepSeek 未登录，请先完成登录', timestamp }
    }

    const hasEditor = Boolean(document.querySelector(DEEPSEEK_SELECTORS.editor))
    if (!hasEditor) {
      if (hasVisibleDeepSeekLoginPrompt()) {
        return { status: 'unauthorized', detail: 'DeepSeek 登录弹窗已弹出，请先完成登录', timestamp }
      }
      return { status: 'error', detail: 'DeepSeek 页面未就绪，等待输入框出现', timestamp }
    }

    if (isDeepSeekGenerating()) {
      return { status: 'generating', detail: 'AI 思考中…', timestamp }
    }

    return { status: 'ready', timestamp }
  }

  function getResponseContainers(): Element[] {
    return [...document.querySelectorAll(DEEPSEEK_SELECTORS.response)].filter(isFinalResponseMarkdown)
  }

  function getAllAssistantReplies(): string[] {
    return keepDeepestResponseContainers(getResponseContainers()).map(container => extractCleanText(container)).filter(Boolean)
  }

  async function fillAndSend(content: string, autoSend = true, _image?: import('./types').ImagePayload): Promise<void> {
    if (checkDeepSeekStatus().status === 'unauthorized') {
      throw new Error(DEEPSEEK_LOGIN_REQUIRED_ERROR)
    }

    const editor = await waitForElement(DEEPSEEK_SELECTORS.editor, inputTimeoutMs)
    if (!(editor instanceof HTMLTextAreaElement)) {
      throw new Error('DeepSeek editor is not a textarea')
    }

    setTextareaText(editor, content)
    if (editor.value.trim() !== content.trim()) {
      throw new Error('DeepSeek editor did not accept the prompt text')
    }

    if (!autoSend) return

    const sendButton = await waitForDeepSeekSendButton(editor, inputTimeoutMs)
    sendButton.click()
  }

  return {
    id: 'deepseek',
    getConversationSnapshot,
    getConversationId,
    getResponseContainers,
    getAllAssistantReplies,
    readResponseText: extractCleanText,
    readResponseMarkdown: extractMarkdownFromDom,
    findResponseContainer,
    checkStatus: () => checkDeepSeekStatus(),
    isGenerating: isDeepSeekGenerating,
    stopGenerating: stopDeepSeekGenerating,
    fillAndSend,
    collectPromptDiagnostics,
  }
}

export function getDeepSeekConversationLocation(href: string): ConversationSnapshot {
  const url = parseSafeDeepSeekUrl(href)
  if (!url) return {}

  return {
    conversationId: extractConversationId(url),
    conversationUrl: url.href,
  }
}

function parseSafeDeepSeekUrl(value: string | undefined): URL | undefined {
  if (!value) return undefined

  try {
    const url = new URL(value)
    return url.protocol === 'https:' && DEEPSEEK_HOSTS.includes(url.hostname) ? url : undefined
  } catch {
    return undefined
  }
}

function extractConversationId(url: URL): string | undefined {
  const match = url.pathname.match(/^\/(?:a\/chat\/s|chat\/s)\/([^/]+)/)
  const conversationId = match?.[1]
  return conversationId ? decodeURIComponent(conversationId) : undefined
}

function setTextareaText(textarea: HTMLTextAreaElement, content: string): void {
  textarea.focus()
  textarea.value = content
  textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: content }))
  textarea.dispatchEvent(new Event('change', { bubbles: true }))
}

async function waitForDeepSeekSendButton(editor: HTMLTextAreaElement, timeoutMs: number): Promise<HTMLElement> {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= timeoutMs) {
    const button = findDeepSeekSendButton(editor)
    if (button) return button
    await new Promise(resolve => window.setTimeout(resolve, 50))
  }

  throw new Error('DeepSeek 发送按钮暂不可用，请稍后重试')
}

function findDeepSeekSendButton(editor: HTMLTextAreaElement): HTMLElement | undefined {
  const composer = editor.closest(DEEPSEEK_SELECTORS.composer) ?? document.body
  const candidates = [...composer.querySelectorAll<HTMLElement>(DEEPSEEK_SELECTORS.sendButton)]
  const labeled = candidates.reverse().find(isDeepSeekSendButton)
  if (labeled) return labeled

  // Fallback: the current DeepSeek layout uses a generic icon-only button (no
  // stable hash/class) sitting at the bottom-right of the composer. Detect it
  // semantically: visible, enabled, icon-only, near the editor in size, and
  // positioned below/right of the input area.
  const candidates2 = [...composer.querySelectorAll<HTMLElement>('button, [role="button"]')]
    .reverse()
    .filter(isDeepSeekSendButton)
  return candidates2.find(button => isIconOnlySendButton(button, editor))
}

function isIconOnlySendButton(button: HTMLElement, editor: HTMLTextAreaElement): boolean {
  if (button.getAttribute('aria-disabled') === 'true') return false
  if (button instanceof HTMLButtonElement && button.disabled) return false
  if (button.textContent && button.textContent.trim().length > 0) return false
  if (!isVisibleInteractiveElement(button)) return false

  const guess = editor.getBoundingClientRect()
  const rect = button.getBoundingClientRect()
  const hasLayout = rect.width > 0 || rect.height > 0 || guess.width > 0 || guess.height > 0
  if (!hasLayout) return true
  const withinRow = Math.abs(rect.y - guess.y) < guess.height + 20 || rect.y >= guess.y
  const squareish = rect.width > 0 && Math.abs(rect.width - rect.height) < Math.max(rect.width, rect.height) * 0.5
  return withinRow && squareish
}

function isDeepSeekSendButton(element: HTMLElement): boolean {
  if (element.getAttribute('aria-disabled') === 'true') return false
  if (element instanceof HTMLButtonElement && element.disabled) return false
  if (element.classList.contains('ds-toggle-button')) return false
  if (element.classList.contains('ds-locale-toggle-item')) return false
  if (element.classList.contains('ds-btn-nav')) return false
  if (!element.classList.contains('ds-icon-button') && !element.querySelector('.ds-icon')) {
    const text = element.textContent?.trim()
    if (text && /深度思考|智能搜索|搜索|思考/.test(text)) return false
  }
  return isVisibleInteractiveElement(element)
}

function collectPromptDiagnostics(): Record<string, unknown> {
  return {
    href: location.href,
    readyState: document.readyState,
    visibilityState: document.visibilityState,
    title: document.title,
    editorMatches: [...document.querySelectorAll(DEEPSEEK_SELECTORS.editor)].slice(0, 5).map(describeElement),
    sendButtonMatches: [...document.querySelectorAll(DEEPSEEK_SELECTORS.sendButton)].slice(0, 5).map(describeElement),
    visibleButtonSamples: [...document.querySelectorAll('[role="button"], button')].slice(0, 12).map(describeElement),
  }
}

function extractCleanText(node: Node): string {
  return extractCleanTextFromDom(node, { skipTags: SKIP_TAGS })
}

function findResponseContainer(element: Element | null): Element | null {
  const finalMarkdown = findClosestMatchingAncestor(element, DEEPSEEK_SELECTORS.response)
  return finalMarkdown && isFinalResponseMarkdown(finalMarkdown) ? finalMarkdown : null
}

function isFinalResponseMarkdown(element: Element): boolean {
  if (element.closest('.ds-think-content')) return false
  return Boolean(element.closest(DEEPSEEK_SELECTORS.responseContainer))
}

function isDeepSeekGenerating(): boolean {
  return Boolean(findDeepSeekStopButton())
}

function hasVisibleDeepSeekLoginPrompt(): boolean {
  for (const element of document.querySelectorAll<HTMLElement>('button, [role="button"], [role="dialog"]')) {
    if (!isVisibleInteractiveElement(element)) continue
    const text = element.textContent?.trim() ?? ''
    if (/登录|登\s*录|sign\s*in|log\s*in/i.test(text)) return true
  }
  return false
}

function isDeepSeekLoginPath(href: string): boolean {
  try {
    return DEEPSEEK_LOGIN_PATH_PATTERN.test(new URL(href).pathname)
  } catch {
    return false
  }
}

async function stopDeepSeekGenerating(): Promise<boolean> {
  const button = findDeepSeekStopButton()
  if (!button) return false
  button.click()
  return true
}

function findDeepSeekStopButton(): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>('[role="button"], button')].find(button => buttonLabelMatches(button, /stop|stopping|停止|中止/) && isClickableDeepSeekButton(button))
}

function isClickableDeepSeekButton(element: HTMLElement): boolean {
  if (element.getAttribute('aria-disabled') === 'true') return false
  if (element instanceof HTMLButtonElement) return isClickableButton(element)
  return isVisibleInteractiveElement(element)
}

function isVisibleInteractiveElement(element: Element): boolean {
  const style = window.getComputedStyle(element)
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' || style.pointerEvents === 'none') return false
  return true
}
