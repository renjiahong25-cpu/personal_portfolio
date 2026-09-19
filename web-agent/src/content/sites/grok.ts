import type { ChatSiteAdapter, ConversationSnapshot } from './types'
import { keepDeepestResponseContainers } from '../responseContainers'
import { findClickableCopyButton, readResponseTextFromCopyAction } from './clipboardCopy'
import { readEditorText, setContentEditableText } from './contentEditable'
import { extractMarkdownFromDom } from './domMarkdown'
import { buttonLabelMatches, describeElement, extractCleanTextFromDom, findClosestMatchingAncestor } from './domText'
import { isClickableButton } from './waitForElement'

const GROK_HOST = 'grok.com'
const GROK_HOME_URL = `https://${GROK_HOST}/`
const DEFAULT_INPUT_TIMEOUT_MS = 9000
const DEFAULT_CLIPBOARD_TIMEOUT_MS = 900
const DEFAULT_CLIPBOARD_POLL_MS = 40

const GROK_SELECTORS = {
  queryBar: '.query-bar',
  chatInput: '[data-testid="chat-input"]',
  proseMirrorEditor: '[data-testid="chat-input"] .ProseMirror[contenteditable="true"]',
  fallbackEditor: 'textarea, input, [contenteditable="true"][role="textbox"], [contenteditable="true"]',
  sendButton:
    'button[data-testid="chat-submit"], button[data-testid*="send"], button[data-testid*="submit"], button[type="submit"], [role="button"][aria-label*="Send"], [role="button"][aria-label*="Submit"], [role="button"][data-testid*="send"], [role="button"][data-testid*="submit"]',
  response:
    '[data-testid="assistant-message"], [data-message-author-role="assistant"], [data-author="assistant"], [class*="assistant-message"], [class*="assistant"] [class*="message"], [class*="assistant"] [class*="message-bubble"]',
  copyButton: 'button[aria-label*="Copy"], button[data-testid*="copy"], button[title*="Copy"]',
  activityIndicator: '[aria-busy="true"], [data-testid*="thinking"], [data-testid*="generating"]',
}

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'BUTTON', 'TEXTAREA', 'SVG'])

interface GrokAdapterOptions {
  href?: string
  inputTimeoutMs?: number
  clipboardTimeoutMs?: number
  clipboardPollMs?: number
}

export function createGrokAdapter(options: GrokAdapterOptions = {}): ChatSiteAdapter {
  const inputTimeoutMs = options.inputTimeoutMs ?? DEFAULT_INPUT_TIMEOUT_MS
  const clipboardTimeoutMs = options.clipboardTimeoutMs ?? DEFAULT_CLIPBOARD_TIMEOUT_MS
  const clipboardPollMs = options.clipboardPollMs ?? DEFAULT_CLIPBOARD_POLL_MS

  function currentHref(): string {
    return options.href ?? location.href
  }

  function getConversationSnapshot(): ConversationSnapshot {
    return getGrokConversationLocation(currentHref())
  }

  function getConversationId(): string {
    return getConversationSnapshot().conversationId || '__default__'
  }

  function getResponseContainers(): Element[] {
    return [...document.querySelectorAll(GROK_SELECTORS.response)].filter(isLikelyAssistantResponse)
  }

  function getAllAssistantReplies(): string[] {
    return keepDeepestResponseContainers(getResponseContainers()).map(container => extractCleanText(container)).filter(Boolean)
  }

  async function fillAndSend(content: string, autoSend = true, _image?: import('./types').ImagePayload): Promise<void> {
    const editor = await waitForGrokEditor(inputTimeoutMs)

    setGrokEditorText(editor, content)
    if (readGrokEditorText(editor) !== content.trim()) {
      throw new Error('Grok editor did not accept the prompt text')
    }

    if (!autoSend) return

    const sendButton = await waitForGrokSendButton(editor, inputTimeoutMs)
    sendButton.click()
  }

  return {
    id: 'grok',
    getConversationSnapshot,
    getConversationId,
    getResponseContainers,
    getAllAssistantReplies,
    readResponseText: extractCleanText,
    readResponseTextFromCopy: node => readResponseTextFromCopy(node, clipboardTimeoutMs, clipboardPollMs),
    readResponseMarkdown: extractMarkdownFromDom,
    findResponseContainer,
    isGenerating: isGrokGenerating,
    stopGenerating: stopGrokGenerating,
    fillAndSend,
    collectPromptDiagnostics,
  }
}

export function getGrokConversationLocation(href: string): ConversationSnapshot {
  const url = parseSafeGrokUrl(href)
  if (!url) return {}

  return {
    conversationId: extractConversationId(url),
    conversationUrl: url.href,
  }
}

function parseSafeGrokUrl(value: string | undefined): URL | undefined {
  if (!value || !value.startsWith(GROK_HOME_URL)) return undefined

  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname === GROK_HOST ? url : undefined
  } catch {
    return undefined
  }
}

function extractConversationId(url: URL): string | undefined {
  const match = url.pathname.match(/^\/(?:chat|c)\/([^/]+)/)
  const conversationId = match?.[1]
  return conversationId ? decodeURIComponent(conversationId) : undefined
}

function setGrokEditorText(editor: HTMLElement, content: string): void {
  if (editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement) {
    setInputValue(editor, content)
    return
  }
  setContentEditableText(editor, content)
}

function setInputValue(editor: HTMLTextAreaElement | HTMLInputElement, content: string): void {
  editor.focus()
  const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(editor), 'value')
  if (descriptor?.set) descriptor.set.call(editor, content)
  else editor.value = content
  editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: content }))
  editor.dispatchEvent(new Event('change', { bubbles: true }))
}

function readGrokEditorText(editor: HTMLElement): string {
  if (editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement) return editor.value.trim()
  return readEditorText(editor, { normalizeNbsp: true })
}

async function waitForGrokEditor(timeoutMs: number): Promise<HTMLElement> {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= timeoutMs) {
    const editor = findGrokEditor()
    if (editor) return editor
    await waitForNextGrokPoll()
  }

  throw new Error('Grok 输入框暂不可用，请稍后重试')
}

function findGrokEditor(): HTMLElement | undefined {
  const proseMirror = [...document.querySelectorAll<HTMLElement>(GROK_SELECTORS.proseMirrorEditor)].find(isUsableGrokEditor)
  if (proseMirror) return proseMirror

  for (const scope of grokEditorScopes()) {
    const editor = [...scope.querySelectorAll<HTMLElement>(GROK_SELECTORS.fallbackEditor)].find(isUsableGrokEditor)
    if (editor) return editor
  }

  return undefined
}

async function waitForGrokSendButton(editor: HTMLElement, timeoutMs: number): Promise<HTMLElement> {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= timeoutMs) {
    const button = findGrokSendButton(editor)
    if (button) return button
    await waitForNextGrokPoll()
  }

  throw new Error('Grok 发送按钮暂不可用，请稍后重试')
}

function findGrokSendButton(editor: HTMLElement): HTMLElement | undefined {
  for (const scope of grokComposerScopes(editor)) {
    const buttons = [...scope.querySelectorAll<HTMLElement>(GROK_SELECTORS.sendButton)]
    const candidate = buttons.reverse().find(isUsableGrokSendButton)
    if (candidate) return candidate
  }
  return undefined
}

function grokComposerScopes(editor: HTMLElement): Element[] {
  const scopes: Element[] = []

  function addScope(scope: Element | null | undefined): void {
    if (!scope || scopes.includes(scope)) return
    scopes.push(scope)
  }

  addScope(editor.closest(GROK_SELECTORS.queryBar))
  addScope(editor.closest(GROK_SELECTORS.chatInput))
  addScope(editor.closest('form'))
  addScope(document.querySelector(GROK_SELECTORS.queryBar))
  addScope(editor.parentElement)
  addScope(document.body)

  return scopes
}

function isUsableGrokSendButton(button: HTMLElement): boolean {
  if (buttonLabelMatches(button, /voice|voice mode|microphone|dictation|attach|upload|camera|photo|image|file|model select|stop|停止|中止/i)) return false

  if (button instanceof HTMLButtonElement) return isClickableButton(button) && isVisibleInteractiveElement(button)
  if (button.getAttribute('aria-disabled') === 'true') return false

  return isVisibleInteractiveElement(button)
}

function grokEditorScopes(): Element[] {
  const scopes: Element[] = []
  const chatInput = document.querySelector(GROK_SELECTORS.chatInput)
  const queryBar = document.querySelector(GROK_SELECTORS.queryBar)

  function addScope(scope: Element | null | undefined): void {
    if (!scope || scopes.includes(scope)) return
    scopes.push(scope)
  }

  addScope(chatInput)
  addScope(queryBar)
  addScope(chatInput?.closest('form'))
  addScope(queryBar?.closest('form'))
  addScope(document.body)
  return scopes
}

function isUsableGrokEditor(editor: Element | null | undefined): editor is HTMLElement {
  if (!(editor instanceof HTMLElement)) return false
  if (editor.hasAttribute('hidden')) return false
  if (editor.getAttribute('aria-hidden') === 'true') return false
  if (editor.getAttribute('tabindex') === '-1') return false
  if (editor instanceof HTMLInputElement || editor instanceof HTMLTextAreaElement) {
    if (editor.disabled || editor.readOnly) return false
  }
  if (!isVisibleInteractiveElement(editor)) return false
  const contentEditable = editor.isContentEditable || editor.getAttribute('contenteditable') === 'true'
  return contentEditable || editor instanceof HTMLInputElement || editor instanceof HTMLTextAreaElement
}

function collectPromptDiagnostics(): Record<string, unknown> {
  const editorCandidates = [...document.querySelectorAll<HTMLElement>(GROK_SELECTORS.fallbackEditor)]
  const queryBar = document.querySelector(GROK_SELECTORS.queryBar)
  return {
    href: location.href,
    readyState: document.readyState,
    visibilityState: document.visibilityState,
    title: document.title,
    editorMatches: editorCandidates.slice(0, 8).map(describeElement),
    proseMirrorMatches: [...document.querySelectorAll(GROK_SELECTORS.proseMirrorEditor)].slice(0, 5).map(describeElement),
    visibleEditorMatches: editorCandidates.filter(isUsableGrokEditor).slice(0, 5).map(describeElement),
    hiddenEditorSamples: [...document.querySelectorAll('textarea[aria-hidden="true"], textarea[tabindex="-1"]')].slice(0, 3).map(describeElement),
    sendButtonMatches: [...document.querySelectorAll(GROK_SELECTORS.sendButton)].slice(0, 5).map(describeElement),
    queryBarButtonSamples: queryBar ? [...queryBar.querySelectorAll('button')].slice(0, 8).map(describeElement) : [],
    visibleButtonSamples: [...document.querySelectorAll('button')].slice(0, 12).map(describeElement),
  }
}

function extractCleanText(node: Node): string {
  return extractCleanTextFromDom(node, { skipTags: SKIP_TAGS })
}

function findResponseContainer(element: Element | null): Element | null {
  const response = findClosestMatchingAncestor(element, GROK_SELECTORS.response)
  return response && isLikelyAssistantResponse(response) ? response : null
}

async function readResponseTextFromCopy(node: Node, timeoutMs: number, pollMs: number): Promise<string | undefined> {
  return readResponseTextFromCopyAction({ node, timeoutMs, pollMs, findCopyButton })
}

function findCopyButton(response: Element): HTMLButtonElement | undefined {
  return findClickableCopyButton(response.closest(GROK_SELECTORS.response) ?? response.parentElement, GROK_SELECTORS.copyButton)
}

function isLikelyAssistantResponse(element: Element): boolean {
  if (element.closest('form, textarea, [contenteditable="true"]')) return false
  if (element.matches('[data-testid="user-message"], [data-message-author-role="user"], [data-author="user"]')) return false
  return extractCleanText(element).length > 0
}

function isGrokGenerating(): boolean {
  return Boolean(findGrokStopButton() || [...document.querySelectorAll(GROK_SELECTORS.activityIndicator)].find(isVisibleInteractiveElement))
}

async function stopGrokGenerating(): Promise<boolean> {
  const button = findGrokStopButton()
  if (!button) return false
  button.click()
  return true
}

function findGrokStopButton(): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => buttonLabelMatches(button, /stop|stopping|停止|中止/i) && isClickableButton(button) && isVisibleInteractiveElement(button))
}

function isVisibleInteractiveElement(element: Element): boolean {
  if (!(element instanceof HTMLElement)) return false
  if (element.hasAttribute('hidden')) return false
  const style = window.getComputedStyle(element)
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' || style.pointerEvents === 'none') return false
  if (/\bjsdom\b/i.test(window.navigator.userAgent)) return true
  const rect = element.getBoundingClientRect()
  return rect.width > 0 && rect.height > 0
}

function waitForNextGrokPoll(): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, 50))
}
