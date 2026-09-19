import type { ChatSiteAdapter, ConversationSnapshot } from './types'
import { keepDeepestResponseContainers } from '../responseContainers'
import { extractMarkdownFromDom } from './domMarkdown'
import { buttonLabelMatches, describeElement, extractCleanTextFromDom } from './domText'
import { isClickableButton, waitForElement } from './waitForElement'
import { readResponseTextFromCopyAction } from './clipboardCopy'

const DOUBAO_HOST = 'www.doubao.com'
const DOUBAO_ORIGIN = `https://${DOUBAO_HOST}`
const DOUBAO_HOME_URL = `${DOUBAO_ORIGIN}/`
const DEFAULT_INPUT_TIMEOUT_MS = 25000

const DOUBAO_EDITOR_SELECTOR =
  'div.tiptap.ProseMirror[contenteditable="true"], #input-engine-container textarea[placeholder*="发消息"], ' +
  '#input-engine-container [contenteditable="true"], textarea[placeholder*="发消息"], [contenteditable="true"][role="textbox"]'

const DOUBAO_SELECTORS = {
  editor: DOUBAO_EDITOR_SELECTOR,
}

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'BUTTON', 'TEXTAREA', 'SVG'])

interface DoubaoAdapterOptions {
  href?: string
  inputTimeoutMs?: number
  skillButtonTimeoutMs?: number
}

let lastSendTime = 0
let knownImageUrls = new Set<string>()

export function createDoubaoAdapter(options: DoubaoAdapterOptions = {}): ChatSiteAdapter {
  const inputTimeoutMs = options.inputTimeoutMs ?? DEFAULT_INPUT_TIMEOUT_MS
  const skillButtonTimeoutMs = options.skillButtonTimeoutMs ?? 60000

  function currentHref(): string {
    return options.href ?? location.href
  }

  function getConversationSnapshot(): ConversationSnapshot {
    return getDoubaoConversationLocation(currentHref())
  }

  function getConversationId(): string {
    return getConversationSnapshot().conversationId || '__default__'
  }

  function getResponseContainers(): Element[] {
    return collectTextResponseContainers()
  }

  function getAllAssistantReplies(): string[] {
    const containers = keepDeepestResponseContainers(getResponseContainers())
    return containers.map(container => readResponseWithImages(container)).filter(Boolean)
  }

  async function fillAndSend(content: string, autoSend = true, image?: import('./types').ImagePayload): Promise<void> {
    await clickImageSkillButton(skillButtonTimeoutMs)
    await ensureChatPage()
    await delay(3000)

    const editor = await waitForElement(DOUBAO_SELECTORS.editor, inputTimeoutMs)
    if (!(editor instanceof HTMLElement)) {
      throw new Error('Doubao editor not found')
    }
    if (!(editor instanceof HTMLTextAreaElement) && !editor.isContentEditable && editor.getAttribute('contenteditable') !== 'true') {
      throw new Error('Doubao editor is neither textarea nor contenteditable')
    }
    if (image) {
      await pasteImage(editor, image)
      const cleanContent = stripPersonaWrapper(content)
      editor.focus()
      if (editor instanceof HTMLTextAreaElement) {
        editor.value = cleanContent
      } else {
        const p = document.createElement('p')
        p.textContent = cleanContent
        editor.appendChild(p)
      }
      editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: cleanContent }))
      editor.dispatchEvent(new Event('change', { bubbles: true }))
    } else {
      setEditorText(editor, content)
    }

    const actualText = editor instanceof HTMLTextAreaElement ? editor.value : editor.textContent || ''
    if (!image && actualText.trim() !== content.trim()) {
      throw new Error(`Doubao editor did not accept the prompt text. Editor tag: ${editor.tagName}, textContent: "${actualText.slice(0, 200)}"`)
    }

    if (!autoSend) return

    await delay(500 + Math.random() * 500)

    const sendButton = await waitForDoubaoSendButton()
    if (sendButton) {
      sendButton.click()
    } else {
      editor.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter',
        keyCode: 13,
        which: 13,
        code: 'Enter',
        bubbles: true,
        cancelable: true,
      }))
    }

    lastSendTime = Date.now()
    knownImageUrls = new Set(collectAllImageUrls())
  }

  return {
    id: 'doubao',
    getConversationSnapshot,
    getConversationId,
    getResponseContainers,
    getAllAssistantReplies,
    readResponseText: readResponseWithImages,
    readResponseTextFromCopy: readDoubaoResponseWithImages,
    readResponseMarkdown: extractMarkdownFromDom,
    findResponseContainer,
    isGenerating: isDoubaoGenerating,
    stopGenerating: stopDoubaoGenerating,
    fillAndSend,
    collectPromptDiagnostics,
  }
}

// --- URL / Location ---

export function getDoubaoConversationLocation(href: string): ConversationSnapshot {
  const url = parseSafeDoubaoUrl(href)
  if (!url) return {}
  return {
    conversationId: extractConversationId(url),
    conversationUrl: url.href,
  }
}

function parseSafeDoubaoUrl(value: string | undefined): URL | undefined {
  if (!value || (!value.startsWith(DOUBAO_HOME_URL) && !value.startsWith('https://doubao.com/'))) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && (url.hostname === DOUBAO_HOST || url.hostname === 'doubao.com') ? url : undefined
  } catch {
    return undefined
  }
}

function extractConversationId(url: URL): string | undefined {
  const match = url.pathname.match(/^\/chat\/([^/]+)/)
  const conversationId = match?.[1]
  return conversationId ? decodeURIComponent(conversationId) : undefined
}

// --- Helpers ---

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * Resolve the composer/input container for a Doubao editor. The legacy
 * `#input-engine-container` id was removed when Doubao migrated the composer to
 * a TipTap/ProseMirror editor; fall back to the editor's composer root so image
 * paste handling and response-scope exclusion still work.
 */
function findDoubaoInputArea(editor?: HTMLElement): HTMLElement | null {
  const legacy = document.querySelector<HTMLElement>('#input-engine-container')
  if (legacy) return legacy

  const anchor = editor ?? document.querySelector<HTMLElement>(DOUBAO_SELECTORS.editor)
  if (!anchor || !(anchor instanceof HTMLElement)) return null

  const composer = anchor.closest('[class*="items-stretch"], [class*="min-h-24"], [class*="composer"], form')
  return composer instanceof HTMLElement ? composer : anchor
}

async function pasteImage(editor: HTMLElement, image: { data: string; mimeType: string }): Promise<void> {
  const resp = await fetch(`data:${image.mimeType};base64,${image.data}`)
  const blob = await resp.blob()
  const file = new File([blob], `image.${image.mimeType.split('/')[1] || 'png'}`, { type: image.mimeType })
  const dt = new DataTransfer()
  dt.items.add(file)

  const inputArea = findDoubaoInputArea(editor)

  // Set up MutationObserver to catch duplicates as they appear (instant dedup)
  const dedupObserver = inputArea ? setupDedupObserver(inputArea) : null

  editor.focus()
  let uploadOk = false
  // Dispatch on BOTH inputArea and editor — the paste upload handler is split
  // across two elements and only dispatching on both triggers the upload.
  if (inputArea) {
    inputArea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }
  editor.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))

  // Wait for the reference image thumbnail to appear
  if (inputArea) {
    uploadOk = await waitForRefImage(inputArea, 30000)
  }

  if (!uploadOk) {
    console.log('[Web Agent] Reference image not detected, retrying paste...')
    await delay(3000)
    if (inputArea) {
      inputArea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
    }
    editor.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
    if (inputArea) {
      uploadOk = await waitForRefImage(inputArea, 30000)
    }
  }

  if (dedupObserver) dedupObserver.disconnect()

  // Final dedup sweep — keep the LAST container per unique src (the newest one),
  // because the image always uploads twice and the later paste is more reliable.
  if (inputArea) {
    const refContainers = collectRefImageContainers(inputArea)
    if (refContainers.length > 1) {
      const srcToLast = new Map<string, HTMLElement>()
      for (const el of refContainers) {
        const img = el.querySelector<HTMLImageElement>('img[alt="image"]')
        const src = img?.getAttribute('src') || ''
        // Keep overwriting: the last iteration wins
        srcToLast.set(src, el)
      }
      for (const el of refContainers) {
        const img = el.querySelector<HTMLImageElement>('img[alt="image"]')
        const src = img?.getAttribute('src') || ''
        if (srcToLast.get(src) !== el) {
          el.remove()
        }
      }
      console.log('[Web Agent] Kept last reference image per src')
    }
  }

  // Remove inline images from the editor
  editor.querySelectorAll('img').forEach(el => el.remove())

  if (!uploadOk) {
    console.warn('[Web Agent] Proceeding without reference image upload confirmation')
  }
}

function setupDedupObserver(root: Element): MutationObserver | null {
  try {
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        if (mutation.type !== 'childList') continue
        for (const node of mutation.addedNodes) {
          if (!(node instanceof HTMLElement)) continue
          if (!node.querySelector('img[alt="image"]')) continue
          // A new reference image container appeared — remove any earlier dupes,
          // keeping only the newest (last) container per image source.
          const allContainers = collectRefImageContainers(root)
          if (allContainers.length > 1) {
            const srcToLast = new Map<string, HTMLElement>()
            for (const el of allContainers) {
              const img = el.querySelector<HTMLImageElement>('img[alt="image"]')
              const src = img?.getAttribute('src') || ''
              srcToLast.set(src, el)
            }
            for (const el of allContainers) {
              const img = el.querySelector<HTMLImageElement>('img[alt="image"]')
              const src = img?.getAttribute('src') || ''
              if (srcToLast.get(src) !== el) {
                el.remove()
                console.log('[Web Agent] MutationObserver removed earlier dupe')
              }
            }
          }
        }
      }
    })
    observer.observe(root, { childList: true, subtree: true })
    return observer
  } catch {
    return null
  }
}

function collectRefImageContainers(root: Element): HTMLElement[] {
  const results: HTMLElement[] = []
  const seen = new Set<Element>()
  for (const img of root.querySelectorAll<HTMLImageElement>('img[alt="image"]')) {
    if (img.closest('.ProseMirror')) continue
    if (!img.src.startsWith('blob:')) continue
    let el = img.parentElement
    while (el && el !== root && el !== document.body) {
      if (el.classList.length > 0 && [...el.classList].some(c => c.startsWith('container-'))) {
        if (!seen.has(el)) {
          seen.add(el)
          results.push(el)
        }
        break
      }
      el = el.parentElement
    }
  }
  return results
}

function waitForRefImage(container: Element, timeoutMs: number): Promise<boolean> {
  const start = Date.now()
  return new Promise(resolve => {
    const poll = () => {
      if (Date.now() - start > timeoutMs) return resolve(false)
      const found = collectRefImageContainers(container).length > 0
      if (found) return resolve(true)
      setTimeout(poll, 500)
    }
    setTimeout(poll, 2000)
  })
}

function stripPersonaWrapper(content: string): string {
  const userMessageMarker = /(?:用户消息|User message)[：:]\n/
  const match = content.match(userMessageMarker)
  if (!match) return content
  const afterMarker = content.slice(match.index! + match[0].length)
  const responseMarker = /\n\n(?:请以|Reply as)/
  const suffixIndex = afterMarker.search(responseMarker)
  return suffixIndex >= 0 ? afterMarker.slice(0, suffixIndex).trim() : afterMarker.trim()
}

function setEditorText(element: HTMLElement, content: string): void {
  element.focus()
  if (element instanceof HTMLTextAreaElement) {
    element.value = content
  } else if (element.isContentEditable || element.getAttribute('contenteditable') === 'true') {
    element.innerHTML = ''
    const p = document.createElement('p')
    p.textContent = content
    element.appendChild(p)
  }
  element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: content }))
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

// --- Page Navigation ---

async function ensureChatPage(): Promise<void> {
  const editor = document.querySelector(DOUBAO_EDITOR_SELECTOR)
  if (editor) return

  const newChatButton = findNewChatButton()
  if (newChatButton) {
    newChatButton.click()
    await delay(3000)
    return
  }

  const chatLink = document.querySelector<HTMLAnchorElement>('a[href*="/chat/"]')
  if (chatLink) {
    chatLink.click()
    await delay(3000)
  }
}

async function waitForDoubaoSendButton(timeoutMs = 6000): Promise<HTMLElement | null> {
  const startedAt = Date.now()

  const find = (): HTMLElement | null => {
    const button = document.querySelector<HTMLElement>('#flow-end-msg-send')
    if (!button) return null
    if (button.hasAttribute('disabled')) return null
    if (button.getAttribute('aria-disabled') === 'true') return null
    return button
  }

  const immediate = find()
  if (immediate) return immediate

  while (Date.now() - startedAt <= timeoutMs) {
    await delay(150)
    const button = find()
    if (button) return button
  }
  return null
}

function findNewChatButton(): HTMLElement | undefined {
  for (const btn of document.querySelectorAll<HTMLElement>('button, [role="button"], a')) {
    const text = btn.textContent?.trim() || ''
    if (text.includes('新建对话') || text.includes('New Chat') || text.includes('新对话')) {
      if (isClickableButton(btn)) return btn
    }
  }
  return undefined
}

// --- Skill Button ---

async function clickImageSkillButton(skillButtonTimeoutMs: number): Promise<void> {
  const button = await waitForImageSkillButton(skillButtonTimeoutMs)
  if (!button) return
  button.click()
  await delay(2000 + Math.random() * 1000)
}

function findImageSkillButton(): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>('button[data-component-type="skill-item"]')].find(b =>
    b.textContent?.includes('图像生成')
  )
}

async function waitForImageSkillButton(timeoutMs: number): Promise<HTMLElement | undefined> {
  const button = findImageSkillButton()
  if (button) return button
  return new Promise(resolve => {
    const timer = window.setInterval(() => {
      const btn = findImageSkillButton()
      if (btn) {
        window.clearInterval(timer)
        resolve(btn)
      }
    }, 500)
    window.setTimeout(() => {
      window.clearInterval(timer)
      resolve(undefined)
    }, timeoutMs)
  })
}

// --- Response Collection ---

function collectTextResponseContainers(): Element[] {
  const inputArea = findDoubaoInputArea()
  const isInputElement = (el: Element): boolean =>
    Boolean(inputArea && (inputArea === el || inputArea.contains(el))) || Boolean(el.closest('[contenteditable="true"]'))

  const textBlocks = document.querySelectorAll('[data-testid="message-block-container"] .message-content, [data-testid="message-block-container"]')
  const renderNodes = document.querySelectorAll('[data-render-engine="node"]')
  return [...textBlocks, ...renderNodes].filter(el => !isInputElement(el))
}

function collectAllImageUrls(): string[] {
  const urls: string[] = []
  const seen = new Set<string>()
  // Targeted selectors for Doubao's known image patterns
  const selectors = [
    'picture img',
    'picture source[srcset]',
    'img[imagex-type]',
    'img[alt="image"]',
    'img[src*="byteimg.com"]',
    'img[src*="imgext.com"]',
    'img[src*="byteimg"]',
    'source[srcset*="byteimg"]',
    'source[srcset*="imgext"]',
    '[src*="byteimg.com"]',
    '[src*="imgext.com"]',
    'img[src*="s.imgext.com"]',
  ]
  for (const el of document.querySelectorAll<HTMLImageElement | HTMLSourceElement>(selectors.join(', '))) {
    let src = ''
    if (el instanceof HTMLSourceElement) {
      src = el.srcset
    } else if (el instanceof HTMLImageElement) {
      src = el.currentSrc || el.src
    }
    if (src && src.startsWith('http') && !seen.has(src)) {
      seen.add(src)
      urls.push(src)
    }
  }
  // Fallback: any <img> outside the input area with an HTTP src — catches
  // generated images that don't match the known selectors above.
  const inputArea = findDoubaoInputArea()
  for (const img of document.querySelectorAll<HTMLImageElement>('img')) {
    if (img.closest('[contenteditable="true"]')) continue
    if (inputArea && inputArea.contains(img)) continue
    const src = img.currentSrc || img.src || ''
    if (src.startsWith('http') && !seen.has(src)) {
      seen.add(src)
      urls.push(src)
    }
  }
  return urls
}

/**
 * Collect image URLs within the given root element only (container-scoped).
 * Unlike the page-level scan above, this keeps image attribution tied to the
 * message block that each reply lives in, so one reply's images never leak
 * into another reply (and vice versa).
 */
function collectImagesInScope(root: Element): string[] {
  const urls: string[] = []
  const seen = new Set<string>()
  const selectors = [
    'picture img',
    'picture source[srcset]',
    'img[imagex-type]',
    'img[alt="image"]',
    'img[src*="byteimg.com"]',
    'img[src*="imgext.com"]',
    'img[src*="byteimg"]',
    'source[srcset*="byteimg"]',
    'source[srcset*="imgext"]',
    '[src*="byteimg.com"]',
    '[src*="imgext.com"]',
    'img[src*="s.imgext.com"]',
  ]
  for (const el of root.querySelectorAll<HTMLImageElement | HTMLSourceElement>(selectors.join(', '))) {
    let src = ''
    if (el instanceof HTMLSourceElement) {
      src = el.srcset
    } else if (el instanceof HTMLImageElement) {
      src = el.currentSrc || el.src
    }
    if (src && src.startsWith('http') && !seen.has(src)) {
      seen.add(src)
      urls.push(src)
    }
  }
  // Fallback: any <img> with an HTTP src inside the scope — catches generated
  // images that don't match the known selectors above.
  for (const img of root.querySelectorAll<HTMLImageElement>('img')) {
    const src = img.currentSrc || img.src || ''
    if (src.startsWith('http') && !seen.has(src)) {
      seen.add(src)
      urls.push(src)
    }
  }
  return urls
}

/**
 * Resolve the message block that a node belongs to. The block is the smallest
 * container that holds BOTH the text and the images of a single Doubao reply,
 * so text and images are always read atomically from the same scope.
 *
 * Priority: semantic message block > render-engine node > list item fallback.
 */
function findMessageBlock(node: Node): Element | null {
  let current: Element | null = node instanceof Element ? node : node.parentElement
  while (current) {
    if (current.matches('[data-testid="message-block-container"]')) return current
    if (current.matches('[data-render-engine="node"]')) return current
    current = current.parentElement
  }

  const listContainer = document.querySelector('[class*="list_items"]')
  if (!listContainer) return null
  for (const child of listContainer.children) {
    if (child.contains(node) || child === node) return child
  }
  return null
}

/**
 * Collect the images that belong to the reply containing `node`, keeping them
 * scoped to the message block. Text and images are always read atomically from
 * the same container so one reply's images never leak into another reply.
 *
 * The page-level scan is used ONLY as a last resort when no message block can
 * be resolved (unexpected DOM layout), since a per-reply scope is what keeps
 * image attribution correct.
 */
function collectReplyImages(node: Node): string[] {
  const block = findMessageBlock(node)
  if (block) return collectImagesInScope(block).filter(url => !knownImageUrls.has(url))

  const globalUrls = collectAllImageUrls().filter(url => !knownImageUrls.has(url))
  return globalUrls
}

// --- Read Response ---

function readResponseWithImages(node: Node): string {
  const text = extractCleanTextFromDom(node, { skipTags: SKIP_TAGS })
  const cleanedText = text.replace(/在此处拖放文件文件数量：最多 50 个, 文件类型：[\w, ]+/g, '').trim()
  const newUrls = collectReplyImages(node)
  if (newUrls.length === 0) return cleanedText
  const images = newUrls.map(url => `![image](${url})`).join('\n')
  return cleanedText ? `${cleanedText}\n\n${images}` : images
}

function findDoubaoCopyButton(response: Element): HTMLButtonElement | undefined {
  const btn = response.querySelector<HTMLButtonElement>(
    'button[aria-label*="copy" i], button[aria-label*="复制" i], ' +
    '[data-testid*="copy"] button, button[data-testid*="copy"]'
  )
  return btn ?? undefined
}

async function readDoubaoResponseWithImages(node: Node): Promise<string | undefined> {
  // Method 1: clipboard copy for the text (most reliable text source)
  const copied = await readResponseTextFromCopyAction({
    node,
    timeoutMs: 15000,
    pollMs: 500,
    findCopyButton: findDoubaoCopyButton,
  })

  // Images are always read from the container scope (message block), independent
  // of whether the copy succeeded — copy may carry text only on generations
  // without an image, and the DOM may lag behind the copy text.
  let allUrls = collectReplyImages(node)
  const deadline = Date.now() + 30000
  while (allUrls.length === 0 && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000))
    allUrls = collectReplyImages(node)
  }

  const cleanedText = (() => {
    const trimmed = copied?.trim()
    if (trimmed) return trimmed
    const text = extractCleanTextFromDom(node, { skipTags: SKIP_TAGS })
    return text.replace(/在此处拖放文件文件数量：最多 50 个, 文件类型：[\w, ]+/g, '').trim()
  })()

  if (allUrls.length === 0) return cleanedText || undefined
  const images = allUrls.map(url => `![image](${url})`).join('\n')
  return cleanedText ? `${cleanedText}\n\n${images}` : images
}

// --- Find Container ---

function findResponseContainer(element: Element | null): Element | null {
  if (!element) return null

  const block = findMessageBlock(element)
  if (!block) return null
  if (block.textContent?.includes('在此处拖放文件') || block.textContent?.includes('文件数量')) return null

  return block
}

// --- Generating / Stop ---

function isDoubaoGenerating(): boolean {
  if (findDoubaoStopButton()) return true

  const loadingIndicators = document.querySelectorAll('[class*="loading"], [class*="generating"], [class*="thinking"], [class*="creating"]')
  for (const el of loadingIndicators) {
    if (isVisibleInteractiveElement(el)) return true
  }

  const sendingIndicator = document.querySelector('[class*="sending"], [class*="progress"], [class*="pending"]')
  if (sendingIndicator && isVisibleInteractiveElement(sendingIndicator)) return true

  const images = document.querySelectorAll<HTMLImageElement>('picture > img[src], img[alt="image"], img[imagex-type], img[src*="byteimg.com"]')
  for (const img of images) {
    if (!img.src || !img.src.startsWith('http')) continue
    if (img.getAttribute('loading') === 'lazy') continue
    if (img.complete && img.naturalWidth > 0 && img.naturalHeight > 0) continue
    if (img.naturalWidth === 0 || img.naturalHeight === 0) return true
  }

  if (lastSendTime > 0 && Date.now() - lastSendTime < 10000) return true

  const latestContainers = keepDeepestResponseContainers(collectTextResponseContainers())
  const hasGeneratingText = latestContainers.slice(-2).some(c => /正在为你生成|正在为您生成|生成中/.test(c.textContent || ''))
  if (hasGeneratingText) {
    const hasAnyImage = document.querySelector<HTMLImageElement>('img[src*="byteimg.com"], img[imagex-type], picture img[src]')
    const hasImageGrid = document.querySelector('[class*="image-box-grid"]')
    if (!hasAnyImage && !hasImageGrid) return true
  }

  const latestText = latestContainers.slice(-1).map(c => c.textContent || '').join(' ')
  const hasImagePending = /处理.*图片|图片.*处理|生成.*图片|图片.*生成/.test(latestText) && !/!\[image\]/.test(latestText)
  if (hasImagePending) {
    const hasAnyImage = document.querySelector<HTMLImageElement>('img[src*="byteimg.com"], img[imagex-type], picture img[src]')
    if (!hasAnyImage) return true
  }

  return false
}

async function stopDoubaoGenerating(): Promise<boolean> {
  const button = findDoubaoStopButton()
  if (!button) return false
  button.click()
  return true
}

function findDoubaoStopButton(): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>('[role="button"], button')].find(button =>
    buttonLabelMatches(button, /stop|stopping|停止|中止/) && isClickableButton(button) && isVisibleInteractiveElement(button)
  )
}

function isVisibleInteractiveElement(element: Element): boolean {
  const style = window.getComputedStyle(element)
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' || style.pointerEvents === 'none') return false
  return true
}

// --- Diagnostics ---

function collectPromptDiagnostics(): Record<string, unknown> {
  return {
    href: location.href,
    readyState: document.readyState,
    visibilityState: document.visibilityState,
    title: document.title,
    editorMatches: [...document.querySelectorAll(DOUBAO_SELECTORS.editor)].slice(0, 5).map(describeElement),
    inputAreas: [findDoubaoInputArea()].filter((el): el is HTMLElement => Boolean(el)).map(describeElement),
    sendButton: describeElementSafe(document.querySelector('#flow-end-msg-send')),
    visibleButtonSamples: [...document.querySelectorAll('[role="button"], button')].slice(0, 12).map(describeElement),
  }
}

function describeElementSafe(element: Element | null): Record<string, unknown> | null {
  return element ? describeElement(element) : null
}
