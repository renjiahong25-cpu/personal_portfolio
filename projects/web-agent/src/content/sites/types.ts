import type { SiteStatus } from '../../group/runtimeProtocol'

export interface ImagePayload {
  data: string
  mimeType: string
}

export interface SiteStatusInfo {
  status: SiteStatus
  detail?: string
  timestamp: number
}

export interface ConversationSnapshot {
  conversationId?: string
  conversationUrl?: string
}

export interface ChatSiteAdapter {
  readonly id: string
  getConversationSnapshot(): ConversationSnapshot
  getConversationId(): string
  getResponseContainers(): Element[]
  getAllAssistantReplies(): string[]
  readResponseText(node: Node): string
  readResponseTextFromCopy?(node: Node): Promise<string | undefined>
  readResponseMarkdown?(node: Node): string
  findResponseContainer(element: Element | null): Element | null
  isGenerating(): boolean
  checkStatus?(): SiteStatusInfo
  stopGenerating(): Promise<boolean>
  fillAndSend(content: string, autoSend?: boolean, image?: ImagePayload): Promise<void>
  collectPromptDiagnostics(): Record<string, unknown>
}
