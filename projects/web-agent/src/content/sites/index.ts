import { createClaudeAdapter } from './claude'
import { createChatGptAdapter } from './chatgpt'
import { createDeepSeekAdapter } from './deepseek'
import { createDoubaoAdapter } from './doubao'
import { createGeminiAdapter } from './gemini'
import { createGrokAdapter } from './grok'
import type { ChatSiteAdapter } from './types'

export function getActiveChatSiteAdapter(): ChatSiteAdapter {
  if (location.hostname === 'claude.ai') return createClaudeAdapter()
  if (location.hostname === 'chat.deepseek.com' || location.hostname === 'www.deepseek.com' || location.hostname === 'deepseek.com') return createDeepSeekAdapter()
  if (location.hostname === 'chatgpt.com' || location.hostname === 'chat.openai.com') return createChatGptAdapter()
  if (location.hostname === 'grok.com') return createGrokAdapter()
  if (location.hostname === 'www.doubao.com' || location.hostname === 'doubao.com') return createDoubaoAdapter()
  return createGeminiAdapter()
}
