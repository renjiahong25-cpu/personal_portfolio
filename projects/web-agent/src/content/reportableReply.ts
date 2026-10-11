import { keepDeepestResponseContainers } from './responseContainers'
import type { ContentLogger } from './runtimeClient'
import type { ChatSiteAdapter } from './sites/types'

export interface ReportableReplyText {
  text: string
  contentFormat?: 'markdown'
}

const LONG_REPLY_TEXT_LENGTH = 160
const SHORT_COPY_TEXT_LENGTH = 40
const MIN_COPY_TO_DOM_TEXT_RATIO = 0.2

export async function resolveReportableReplyText(
  siteAdapter: ChatSiteAdapter,
  element: Element,
  fallbackText: string,
  log: ContentLogger,
): Promise<ReportableReplyText> {
  try {
    const copiedText = await siteAdapter.readResponseTextFromCopy?.(element)
    const trimmedCopiedText = copiedText?.trim()
    if (trimmedCopiedText && !isSuspiciousCopiedReply(trimmedCopiedText, fallbackText)) return { text: trimmedCopiedText, contentFormat: 'markdown' }
    if (trimmedCopiedText) {
      log.warn('reply-copy:ignored-suspicious-short-text', {
        copiedLength: normalizeReplyForLengthCheck(trimmedCopiedText).length,
        domTextLength: normalizeReplyForLengthCheck(fallbackText).length,
      })
    }
  } catch (error) {
    log.warn('reply-copy:failed', { error: error instanceof Error ? error.message : String(error) })
  }

  try {
    const markdownText = siteAdapter.readResponseMarkdown?.(element).trim()
    if (markdownText) return { text: markdownText, contentFormat: 'markdown' }
  } catch (error) {
    log.warn('reply-markdown:failed', { error: error instanceof Error ? error.message : String(error) })
  }

  return { text: fallbackText }
}

export async function readResyncReplyText(
  siteAdapter: ChatSiteAdapter,
  currentContent: string | undefined,
  log: ContentLogger,
): Promise<ReportableReplyText> {
  const candidate = findResyncReplyCandidate(siteAdapter, currentContent, log)
  if (!candidate) throw new Error('当前页面没有可同步的回复内容')
  return resolveReportableReplyText(siteAdapter, candidate.element, candidate.text, log)
}

function findResyncReplyCandidate(siteAdapter: ChatSiteAdapter, currentContent: string | undefined, log: ContentLogger): { element: Element; text: string } | undefined {
  const candidates = keepDeepestResponseContainers(siteAdapter.getResponseContainers())
    .map(element => ({ element, text: siteAdapter.readResponseText(element).trim() }))
    .filter(candidate => candidate.text)

  if (candidates.length === 0) return undefined

  const normalizedCurrent = normalizeReplyForMatch(currentContent)
  const currentLength = normalizedCurrent.length
  if (!normalizedCurrent) {
    const latestCandidate = candidates[candidates.length - 1]
    log.warn('reply-resync:candidate-selected', {
      strategy: 'latest-no-current',
      candidateCount: candidates.length,
      candidateLengths: candidates.map(candidate => normalizeReplyForMatch(candidate.text).length),
      selectedLength: normalizeReplyForMatch(latestCandidate.text).length,
    })
    return latestCandidate
  }

  const longerMatchingCandidate = [...candidates].reverse().find(candidate => {
    const normalizedText = normalizeReplyForMatch(candidate.text)
    return normalizedText.length > currentLength && normalizedText.includes(normalizedCurrent)
  })
  if (longerMatchingCandidate) {
    log.warn('reply-resync:candidate-selected', {
      strategy: 'longer-match',
      currentLength,
      candidateCount: candidates.length,
      candidateLengths: candidates.map(candidate => normalizeReplyForMatch(candidate.text).length),
      selectedLength: normalizeReplyForMatch(longerMatchingCandidate.text).length,
    })
    return longerMatchingCandidate
  }

  const latestCandidate = candidates[candidates.length - 1]
  const latestLength = normalizeReplyForMatch(latestCandidate.text).length
  if (latestLength > currentLength) {
    log.warn('reply-resync:candidate-selected', {
      strategy: 'latest-longer',
      currentLength,
      candidateCount: candidates.length,
      candidateLengths: candidates.map(candidate => normalizeReplyForMatch(candidate.text).length),
      selectedLength: latestLength,
    })
    return latestCandidate
  }

  const matchingCandidate = [...candidates].reverse().find(candidate => {
    const normalizedText = normalizeReplyForMatch(candidate.text)
    return normalizedText.includes(normalizedCurrent) || normalizedCurrent.includes(normalizedText)
  })

  const selectedCandidate = matchingCandidate ?? latestCandidate
  log.warn('reply-resync:candidate-selected', {
    strategy: matchingCandidate ? 'same-or-shorter-match' : 'latest-fallback',
    currentLength,
    candidateCount: candidates.length,
    candidateLengths: candidates.map(candidate => normalizeReplyForMatch(candidate.text).length),
    selectedLength: normalizeReplyForMatch(selectedCandidate.text).length,
  })
  return selectedCandidate
}

function isSuspiciousCopiedReply(copiedText: string, fallbackText: string): boolean {
  const copiedLength = normalizeReplyForLengthCheck(copiedText).length
  const fallbackLength = normalizeReplyForLengthCheck(fallbackText).length
  if (fallbackLength < LONG_REPLY_TEXT_LENGTH) return false
  if (copiedLength < SHORT_COPY_TEXT_LENGTH) return true
  return copiedLength / fallbackLength < MIN_COPY_TO_DOM_TEXT_RATIO
}

function normalizeReplyForLengthCheck(text: string): string {
  return text.replace(/\s+/g, '')
}

function normalizeReplyForMatch(text: string | undefined): string {
  return (text ?? '').replace(/\s+/g, '')
}
