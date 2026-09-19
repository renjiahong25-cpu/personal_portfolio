// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { countResponseContainers, keepDeepestResponseContainers } from './responseContainers'

describe('keepDeepestResponseContainers', () => {
  it('keeps the inner reply container when Gemini reports both outer and inner nodes', () => {
    document.body.innerHTML = `
      <model-response id="outer">
        <message-content id="inner">你好</message-content>
      </model-response>
    `
    const outer = document.getElementById('outer')!
    const inner = document.getElementById('inner')!

    expect(keepDeepestResponseContainers([outer, inner])).toEqual([inner])
  })

  it('keeps separate sibling replies', () => {
    document.body.innerHTML = `
      <message-content id="first">第一条</message-content>
      <message-content id="second">第二条</message-content>
    `
    const first = document.getElementById('first')!
    const second = document.getElementById('second')!

    expect(keepDeepestResponseContainers([first, second])).toEqual([first, second])
  })
})

describe('countResponseContainers', () => {
  it('returns 0 for empty container list', () => {
    expect(countResponseContainers([])).toBe(0)
  })

  it('returns correct count for multiple containers', () => {
    document.body.innerHTML = `
      <message-content id="a">A</message-content>
      <message-content id="b">B</message-content>
      <message-content id="c">C</message-content>
    `
    const containers = [
      document.getElementById('a')!,
      document.getElementById('b')!,
      document.getElementById('c')!,
    ]
    expect(countResponseContainers(containers)).toBe(3)
  })

  it('returns correct count after DOM rebuild (positional baseline)', () => {
    // Simulate: before send there were 2 containers
    document.body.innerHTML = `
      <message-content id="old1">旧回复1</message-content>
      <message-content id="old2">旧回复2</message-content>
    `
    const oldContainers = Array.from(document.querySelectorAll('message-content'))
    const baselineCount = countResponseContainers(oldContainers)
    expect(baselineCount).toBe(2)

    // DOM rebuild: old elements are replaced by new ones
    document.body.innerHTML = `
      <message-content id="rebuilt1">旧回复1</message-content>
      <message-content id="rebuilt2">旧回复2</message-content>
      <message-content id="new1">新回复</message-content>
    `
    const newContainers = Array.from(document.querySelectorAll('message-content'))
    // The count is now 3, and the new reply is at index >= baselineCount
    const newReplyElement = document.getElementById('new1')!
    const newReplyIndex = newContainers.indexOf(newReplyElement)
    expect(newReplyIndex).toBeGreaterThanOrEqual(baselineCount)
  })
})
