import { describe, expect, it } from 'vitest'
import { createReplyTracker } from './replyTracker'

describe('createReplyTracker', () => {
  it('does not report replies that were seeded from existing page history', () => {
    const tracker = createReplyTracker()
    tracker.seed('conv-a', ['old answer 1', 'old answer 2'])

    expect(tracker.consumeIfNew('conv-a', 'old answer 1')).toBe(false)
    expect(tracker.consumeIfNew('conv-a', 'old answer 2')).toBe(false)
  })

  it('does not report stored role replies after an iframe reload restores old DOM later', () => {
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['初始化时的旧回复'])

    expect(tracker.consumeIfNewForMessage('conv-a', '初始化时的旧回复', 'msg-new')).toBe(false)
    expect(tracker.consumeIfNewForMessage('conv-b', '初始化时的旧回复', 'msg-newer')).toBe(true)
    expect(tracker.consumeIfNewForMessage('conv-a', '针对新消息的回复', 'msg-new')).toBe(true)
  })

  it('reports a new reply once when Gemini rerenders history before the latest answer', () => {
    const tracker = createReplyTracker()
    tracker.seed('conv-a', ['old answer 1', 'old answer 2'])

    const reported = ['old answer 1', 'old answer 2', 'new answer'].filter(text => tracker.consumeIfNew('conv-a', text))

    expect(reported).toEqual(['new answer'])
    expect(tracker.consumeIfNew('conv-a', 'new answer')).toBe(false)
  })

  it('consumes only one reply for a single sent message id', () => {
    const tracker = createReplyTracker()

    expect(tracker.consumeIfNewForMessage('conv-a', 'outer extracted answer', 'msg-1')).toBe(true)
    expect(tracker.consumeIfNewForMessage('conv-a', 'inner extracted answer', 'msg-1')).toBe(false)
    expect(tracker.consumeIfNewForMessage('conv-a', 'late answer without message id', undefined)).toBe(false)
  })
})

describe('Bug 2: same text in different rounds should not be hash-deduplicated', () => {
  it('allows the same short text reply in different rounds when messageId differs', () => {
    const tracker = createReplyTracker()

    // Round 1: AI says "好的" with messageId msg-1
    expect(tracker.consumeIfNew('conv-a', '好的', 'msg-1')).toBe(true)

    // Round 2: AI says "好的" again with a different messageId msg-2
    // This should NOT be blocked by hash dedup — it's a different round
    expect(tracker.consumeIfNew('conv-a', '好的', 'msg-2')).toBe(true)
  })

  it('prevents the same messageId from being consumed twice', () => {
    const tracker = createReplyTracker()

    expect(tracker.consumeIfNew('conv-a', 'some reply', 'msg-1')).toBe(true)
    expect(tracker.consumeIfNew('conv-a', 'different reply', 'msg-1')).toBe(false)
  })

  it('falls back to text hash dedup when no messageId is provided', () => {
    const tracker = createReplyTracker()

    // First consumption without messageId succeeds
    expect(tracker.consumeIfNew('conv-a', '好的')).toBe(true)

    // Same text without messageId is blocked by hash
    expect(tracker.consumeIfNew('conv-a', '好的')).toBe(false)

    // But with a messageId it should still pass (messageId takes priority)
    expect(tracker.consumeIfNew('conv-a', '好的', 'msg-new')).toBe(true)
  })

  it('consumeIfNewForMessage allows same text with different messageIds', () => {
    const tracker = createReplyTracker()

    // Round 1
    expect(tracker.consumeIfNewForMessage('conv-a', '好的', 'msg-1')).toBe(true)
    // Round 2 with same text but different messageId
    expect(tracker.consumeIfNewForMessage('conv-a', '好的', 'msg-2')).toBe(true)
    // Round 2 duplicate attempt with same messageId
    expect(tracker.consumeIfNewForMessage('conv-a', '好的', 'msg-2')).toBe(false)
  })

  it('globally seeded replies are blocked for the same conversation even with messageId (cross-session protection)', () => {
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['初始化时的旧回复'])

    expect(tracker.consumeIfNew('conv-a', '初始化时的旧回复')).toBe(false)
    expect(tracker.consumeIfNew('conv-a', '初始化时的旧回复', 'msg-new')).toBe(false)
    expect(tracker.consumeIfNew('conv-b', '初始化时的旧回复', 'msg-other')).toBe(true)
  })

  it('allows repeated same-text replies once a new prompt is delivered (你好 scenario)', () => {
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['你好！我是 DeepSeek，很高兴为你服务。'])

    expect(tracker.consumeIfNewForMessage('conv-a', '你好！我是 DeepSeek，很高兴为你服务。', 'msg-round-1')).toBe(false)

    tracker.releaseGlobalBarrier('conv-a')

    expect(tracker.consumeIfNewForMessage('conv-a', '你好！我是 DeepSeek，很高兴为你服务。', 'msg-round-2')).toBe(true)
    expect(tracker.consumeIfNewForMessage('conv-a', '你好！我是 DeepSeek，很高兴为你服务。', 'msg-round-2')).toBe(false)
  })

  it('releasing one conversation barrier does not affect another conversation', () => {
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['旧回复A'])
    tracker.seedGlobal('conv-b', ['旧回复B'])
    tracker.releaseGlobalBarrier('conv-a')

    expect(tracker.consumeIfNewForMessage('conv-a', '旧回复A', 'msg-1')).toBe(true)
    expect(tracker.consumeIfNewForMessage('conv-b', '旧回复B', 'msg-2')).toBe(false)
  })

  it('same text in different rounds is allowed when seeded via per-conversation seed (not global)', () => {
    const tracker = createReplyTracker()
    // Per-conversation seed: "好的" was seen in conv-a, but a new messageId means a new round
    tracker.seed('conv-a', ['好的'])

    // Without messageId: blocked by per-conversation hash
    expect(tracker.consumeIfNew('conv-a', '好的')).toBe(false)
    // With a new messageId: allowed (Bug 2 fix — different round)
    expect(tracker.consumeIfNew('conv-a', '好的', 'msg-round-2')).toBe(true)
  })
})
