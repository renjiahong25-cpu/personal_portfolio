import { keepDeepestResponseContainers } from './responseContainers'

interface FindLatestCompensationReplyInput {
  containers: Element[]
  readText: (element: Element) => string
  isBaseline: (text: string, element: Element) => boolean
  consume: (text: string, element: Element) => boolean
}

export interface CompensationReply {
  element: Element
  text: string
}

export function findLatestCompensationCandidate(input: Omit<FindLatestCompensationReplyInput, 'consume'>): CompensationReply | undefined {
  return keepDeepestResponseContainers(input.containers)
    .map(element => ({ element, text: input.readText(element).trim() }))
    .filter(candidate => candidate.text && !input.isBaseline(candidate.text, candidate.element))
    .reverse()[0]
}

export function findLatestCompensationReply(input: FindLatestCompensationReplyInput): CompensationReply | undefined {
  const candidates = keepDeepestResponseContainers(input.containers)
    .map(element => ({ element, text: input.readText(element).trim() }))
    .filter(candidate => candidate.text && !input.isBaseline(candidate.text, candidate.element))

  for (const candidate of candidates.reverse()) {
    if (input.consume(candidate.text, candidate.element)) return candidate
  }

  return undefined
}
