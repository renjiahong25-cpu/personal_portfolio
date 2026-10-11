import { isClickableButton } from './waitForElement'

export async function readResponseTextFromCopyAction(options: {
  node: Node
  timeoutMs: number
  pollMs: number
  findCopyButton(response: Element): HTMLButtonElement | undefined
}): Promise<string | undefined> {
  if (options.node.nodeType !== Node.ELEMENT_NODE) return undefined

  const copyButton = options.findCopyButton(options.node as Element)
  const clipboard = navigator.clipboard
  if (!copyButton || !clipboard?.readText) return undefined

  let previousText: string
  try {
    previousText = await clipboard.readText()
  } catch {
    return undefined
  }

  try {
    copyButton.click()
    const copiedText = await waitForClipboardText(previousText, options.timeoutMs, options.pollMs)
    return copiedText?.trim() || undefined
  } catch {
    return undefined
  } finally {
    if (clipboard.writeText) {
      clipboard.writeText(previousText).catch(() => undefined)
    }
  }
}

export function findClickableCopyButton(scope: Element | null, selectors: string): HTMLButtonElement | undefined {
  const copyButton = scope?.querySelector<HTMLButtonElement>(selectors)
  return copyButton && isClickableButton(copyButton) ? copyButton : undefined
}

function waitForClipboardText(previousText: string | undefined, timeoutMs: number, pollMs: number): Promise<string | undefined> {
  const clipboard = navigator.clipboard
  if (!clipboard?.readText) return Promise.resolve(undefined)

  return new Promise(resolve => {
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      clipboard
        .readText()
        .then(text => {
          const trimmed = text.trim()
          if (trimmed && (previousText === undefined || text !== previousText)) {
            window.clearInterval(timer)
            resolve(text)
            return
          }
          if (Date.now() - startedAt >= timeoutMs) {
            window.clearInterval(timer)
            resolve(undefined)
          }
        })
        .catch(() => {
          window.clearInterval(timer)
          resolve(undefined)
        })
    }, pollMs)
  })
}
