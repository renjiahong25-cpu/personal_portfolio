// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { createReplyTracker } from './replyTracker'
import { findLatestCompensationCandidate, findLatestCompensationReply } from './replyCompensation'

describe('findLatestCompensationReply', () => {
  it('returns the latest unreported reply while ignoring restored DOM history', () => {
    document.body.innerHTML = `
      <message-content id="old">初始化时的旧回复</message-content>
      <message-content id="new">针对新消息的补偿回复</message-content>
    `
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['初始化时的旧回复'])

    const result = findLatestCompensationReply({
      containers: [...document.querySelectorAll('message-content')],
      readText: element => element.textContent ?? '',
      isBaseline: () => false,
      consume: text => tracker.consumeIfNewForMessage('conv-a', text, 'msg-1'),
    })

    expect(result?.text).toBe('针对新消息的补偿回复')
    expect(result?.element.id).toBe('new')
  })

  it('returns nothing when every visible reply is baseline or already reported', () => {
    document.body.innerHTML = `
      <message-content id="baseline">发送前已有回复</message-content>
      <message-content id="old">初始化时的旧回复</message-content>
    `
    const tracker = createReplyTracker()
    tracker.seedGlobal('conv-a', ['初始化时的旧回复'])

    const result = findLatestCompensationReply({
      containers: [...document.querySelectorAll('message-content')],
      readText: element => element.textContent ?? '',
      isBaseline: (_text, element) => element.id === 'baseline',
      consume: text => tracker.consumeIfNewForMessage('conv-a', text, 'msg-1'),
    })

    expect(result).toBeUndefined()
  })

  it('can inspect the latest non-baseline reply without consuming it', () => {
    document.body.innerHTML = `
      <message-content id="baseline">发送前已有回复</message-content>
      <message-content id="new">监听漏掉的新回复</message-content>
    `
    const tracker = createReplyTracker()

    const candidate = findLatestCompensationCandidate({
      containers: [...document.querySelectorAll('message-content')],
      readText: element => element.textContent ?? '',
      isBaseline: (_text, element) => element.id === 'baseline',
    })

    expect(candidate?.text).toBe('监听漏掉的新回复')
    expect(candidate?.element.id).toBe('new')
    expect(tracker.consumeIfNewForMessage('conv-a', '监听漏掉的新回复', 'msg-1')).toBe(true)
  })
})
