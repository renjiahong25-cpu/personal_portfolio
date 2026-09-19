import { normalizeLanguage, translateUi, type TeamLanguage } from '../shared/i18n'

export interface LanguageSettingsControllerDependencies {
  englishButton: HTMLButtonElement
  chineseButton: HTMLButtonElement
  getLanguage(): TeamLanguage
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
  showError(message: string): void
}

export interface LanguageSettingsController {
  registerEvents(): void
  render(): void
}

const TEXT_SOURCES = new WeakMap<Text, string>()
const ATTRIBUTE_SOURCES = new WeakMap<Element, Map<string, string>>()
const TRANSLATED_ATTRIBUTES = ['aria-label', 'title', 'placeholder', 'data-tooltip']
const SKIP_SELECTOR = [
  'script',
  'style',
  'textarea',
  'input',
  '[contenteditable="true"]',
  '.ProseMirror',
  '.notes-editor',
  '.message-body',
  '.message-name-text',
  '.message-mention',
  '.message-time-divider',
  '.reference-box',
].join(', ')

export function createLanguageSettingsController(deps: LanguageSettingsControllerDependencies): LanguageSettingsController {
  let registered = false
  let pendingLanguage: TeamLanguage | undefined

  function render(): void {
    const language = pendingLanguage ?? normalizeLanguage(deps.getLanguage())
    deps.englishButton.setAttribute('aria-pressed', String(language === 'en'))
    deps.chineseButton.setAttribute('aria-pressed', String(language === 'zh-CN'))
    applyTeamLanguage(language)
  }

  function registerEvents(): void {
    if (registered) return
    registered = true
    deps.englishButton.addEventListener('click', () => updateLanguage('en'))
    deps.chineseButton.addEventListener('click', () => updateLanguage('zh-CN'))
  }

  function updateLanguage(language: TeamLanguage): void {
    if ((pendingLanguage ?? normalizeLanguage(deps.getLanguage())) === language) return
    pendingLanguage = language
    render()
    deps.runCommand('GROUP_SETTINGS_UPDATE', { language })
      .then(() => {
        if (pendingLanguage === language) pendingLanguage = undefined
        render()
      })
      .catch(error => {
        if (pendingLanguage === language) pendingLanguage = undefined
        render()
        deps.showError(error instanceof Error ? error.message : String(error))
      })
  }

  return { registerEvents, render }
}

export function applyTeamLanguage(language: TeamLanguage, root: ParentNode = document): void {
  const normalized = normalizeLanguage(language)
  const doc = root instanceof Document ? root : root.ownerDocument ?? document
  doc.documentElement.lang = normalized
  doc.documentElement.dataset.language = normalized

  const translationRoot = root instanceof Document ? root.body : root
  if (!translationRoot) return
  translateTextNodes(translationRoot, normalized, doc)
  translateAttributes(translationRoot, normalized)
}

function translateTextNodes(root: ParentNode, language: TeamLanguage, doc: Document): void {
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.textContent?.trim()) return NodeFilter.FILTER_REJECT
      const parent = node.parentElement
      if (!parent || parent.closest(SKIP_SELECTOR)) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const nodes: Text[] = []
  while (walker.nextNode()) {
    nodes.push(walker.currentNode as Text)
  }

  for (const node of nodes) {
    const source = TEXT_SOURCES.get(node) ?? node.textContent ?? ''
    if (!TEXT_SOURCES.has(node)) TEXT_SOURCES.set(node, source)
    node.textContent = translateTextNode(source, language)
  }
}

function translateAttributes(root: ParentNode, language: TeamLanguage): void {
  const elements = root instanceof Element ? [root, ...root.querySelectorAll('*')] : [...root.querySelectorAll('*')]
  for (const element of elements) {
    if (element.closest(SKIP_SELECTOR) && !element.matches('input, textarea')) continue
    for (const attribute of TRANSLATED_ATTRIBUTES) {
      const value = element.getAttribute(attribute)
      if (!value) continue
      const sources = attributeSourcesFor(element)
      const source = sources.get(attribute) ?? value
      sources.set(attribute, source)
      element.setAttribute(attribute, translateUi(source, language))
    }
  }
}

function attributeSourcesFor(element: Element): Map<string, string> {
  const existing = ATTRIBUTE_SOURCES.get(element)
  if (existing) return existing
  const next = new Map<string, string>()
  ATTRIBUTE_SOURCES.set(element, next)
  return next
}

function translateTextNode(source: string, language: TeamLanguage): string {
  if (language === 'zh-CN') return source
  const leading = source.match(/^\s*/)?.[0] ?? ''
  const trailing = source.match(/\s*$/)?.[0] ?? ''
  const trimmed = source.trim()
  if (!trimmed) return source
  return `${leading}${translateUi(trimmed, language)}${trailing}`
}
