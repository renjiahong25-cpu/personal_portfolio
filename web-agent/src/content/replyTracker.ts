function hashStr(value: string): number {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (Math.imul(31, hash) + value.charCodeAt(index)) | 0
  }
  return hash >>> 0
}

function replyKey(conversationId: string, text: string): string {
  return String(hashStr(`${conversationId}:${text.trim()}`))
}

export function createReplyTracker() {
  const seenReplyHashes = new Set<string>()
  const globalSeenByConversation = new Map<string, Set<string>>()
  const consumedMessageIds = new Set<string>()

  function globalKey(conversationId: string | undefined): string {
    return conversationId || '*'
  }

  return {
    seed(conversationId: string, replies: string[]): void {
      for (const reply of replies) {
        const trimmed = reply.trim()
        if (trimmed) seenReplyHashes.add(replyKey(conversationId, trimmed))
      }
    },

    seedGlobal(conversationId: string | undefined, replies: string[]): void {
      for (const reply of replies) {
        const trimmed = reply.trim()
        if (!trimmed) continue
        const key = globalKey(conversationId)
        let set = globalSeenByConversation.get(key)
        if (!set) {
          set = new Set()
          globalSeenByConversation.set(key, set)
        }
        set.add(String(hashStr(trimmed)))
      }
    },

    /**
     * 当某会话的提示词被真正下发到页面后，旧文本闸门对该会话失效。
     * 这样同文本的"新回复"（如重复的"你好"）可以在新一轮被捕捉，
     * 而 iframe 重载后重播的旧历史（未下发新提示词）仍会被拦截。
     */
    releaseGlobalBarrier(conversationId: string | undefined): void {
      globalSeenByConversation.delete(globalKey(conversationId))
    },

    /**
     * Check if a reply is new and consume it.
     *
     * Bug 2 fix: messageId-based deduplication takes priority over per-conversation text hash.
     * When a messageId is provided, we first check if this messageId has already
     * been consumed. If the messageId is new, the reply is accepted even if the
     * per-conversation text hash was seen before (e.g., AI gives "好的" in multiple rounds).
     *
     * However, globally seeded reply texts (seedGlobal) still act as a barrier
     * for the SAME conversation until a new prompt is delivered (releaseGlobalBarrier).
     * This preserves iframe-reload protection while allowing repeated text like
     * "你好" in a new round once the prompt actually reaches the page.
     *
     * Priority order:
     * 1. messageId dedup (if provided) — takes precedence over per-conversation hash
     * 2. same-conversation globally seeded hashes — block until released by a delivered prompt
     * 3. per-conversation seenReplyHashes — skipped when messageId is new
     */
    consumeIfNew(conversationId: string, reply: string, messageId?: string): boolean {
      const trimmed = reply.trim()
      if (!trimmed) return false

      // Barrier: same-conversation globally seeded replies are blocked until the
      // barrier is released by a freshly delivered prompt (releaseGlobalBarrier).
      const globalSet = globalSeenByConversation.get(globalKey(conversationId))
      if (globalSet?.has(String(hashStr(trimmed)))) return false

      // Priority 1: If messageId is provided and already consumed, reject
      if (messageId && consumedMessageIds.has(messageId)) return false

      // Priority 2: If messageId is provided and is new, accept
      // (even if per-conversation text hash was seen — Bug 2 fix)
      if (messageId && !consumedMessageIds.has(messageId)) {
        consumedMessageIds.add(messageId)
        const key = replyKey(conversationId, trimmed)
        seenReplyHashes.add(key)
        return true
      }

      // Fallback: No messageId, use per-conversation text hash deduplication
      const key = replyKey(conversationId, trimmed)
      if (seenReplyHashes.has(key)) return false

      seenReplyHashes.add(key)
      return true
    },

    consumeIfNewForMessage(conversationId: string, reply: string, messageId: string | undefined): boolean {
      if (!messageId) return false
      if (consumedMessageIds.has(messageId)) return false
      if (!this.consumeIfNew(conversationId, reply, messageId)) return false

      return true
    },
  }
}
