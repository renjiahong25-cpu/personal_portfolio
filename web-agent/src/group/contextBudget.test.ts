import { describe, expect, it } from 'vitest'
import { CONTEXT_CHAR_BUDGET_BY_SITE, contextCharBudgetForRole } from './contextBudget'
import type { ChatSite, GroupRole, WebAgentStore } from './types'
import { createDefaultStore } from './store'

describe('context budget', () => {
  it('uses the target site budget when the stored default is smaller', () => {
    const store = makeStore()

    expect(contextCharBudgetForRole(store, makeRole('gemini'))).toBe(CONTEXT_CHAR_BUDGET_BY_SITE.gemini)
    expect(contextCharBudgetForRole(store, makeRole('chatgpt'))).toBe(CONTEXT_CHAR_BUDGET_BY_SITE.chatgpt)
    expect(contextCharBudgetForRole(store, makeRole('claude'))).toBe(CONTEXT_CHAR_BUDGET_BY_SITE.claude)
    expect(contextCharBudgetForRole(store, makeRole('deepseek'))).toBe(CONTEXT_CHAR_BUDGET_BY_SITE.deepseek)
    expect(contextCharBudgetForRole(store, makeRole('grok'))).toBe(CONTEXT_CHAR_BUDGET_BY_SITE.grok)
  })

  it('keeps a larger user configured budget', () => {
    const store = makeStore()
    store.settings.maxContextChars = 2_000_000

    expect(contextCharBudgetForRole(store, makeRole('chatgpt'))).toBe(2_000_000)
  })

  it('infers external model budgets from provider and model names', () => {
    const store = makeStore()
    store.settings.externalModelsById = {
      'model-claude': { id: 'model-claude', name: 'OpenRouter', format: 'openai', baseUrl: 'https://openrouter.ai/api/v1', apiKey: 'key', modelName: 'anthropic/claude-sonnet-4.6', createdAt: 1, updatedAt: 1 },
      'model-deepseek': { id: 'model-deepseek', name: 'DeepSeek', format: 'openai', baseUrl: 'https://api.deepseek.com', apiKey: 'key', modelName: 'deepseek-v4-pro', createdAt: 1, updatedAt: 1 },
    }

    expect(contextCharBudgetForRole(store, makeExternalRole('model-claude'))).toBe(1_000_000)
    expect(contextCharBudgetForRole(store, makeExternalRole('model-deepseek'))).toBe(1_000_000)
  })
})

function makeStore(): WebAgentStore {
  const store = createDefaultStore()
  store.settings.maxContextChars = 6000
  return store
}

function makeRole(chatSite: ChatSite): GroupRole {
  return {
    id: `role-${chatSite}`,
    chatId: 'chat-1',
    name: chatSite,
    chatSite,
    status: 'ready',
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}

function makeExternalRole(externalModelId: string): GroupRole {
  return {
    id: `role-${externalModelId}`,
    chatId: 'chat-1',
    name: externalModelId,
    modelSource: 'external',
    externalModelId,
    chatSite: 'gemini',
    status: 'ready',
    contextCursor: 0,
    createdAt: 1,
    updatedAt: 1,
  }
}
