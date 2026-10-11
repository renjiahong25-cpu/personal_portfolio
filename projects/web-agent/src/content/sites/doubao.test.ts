// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDoubaoAdapter } from './doubao'

function buildMessageBlock(innerHtml: string): HTMLElement {
  const block = document.createElement('div')
  block.setAttribute('data-testid', 'message-block-container')
  block.innerHTML = innerHtml
  return block
}

function attachListContainer(...blocks: HTMLElement[]): HTMLElement {
  const list = document.createElement('div')
  list.setAttribute('class', 'list_items')
  for (const block of blocks) list.appendChild(block)
  document.body.appendChild(list)
  return list
}

describe('Doubao site adapter', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('extracts Doubao conversation ids and normalized urls', () => {
    const adapter = createDoubaoAdapter({ href: 'https://www.doubao.com/chat/abc-123?sid=foo' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: 'abc-123',
      conversationUrl: 'https://www.doubao.com/chat/abc-123?sid=foo',
    })
  })

  it('does not report non-Doubao urls', () => {
    const adapter = createDoubaoAdapter({ href: 'https://doubao.com.evil.example/chat/abc-123' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: undefined,
      conversationUrl: undefined,
    })
  })

  it('writes prompt text into the new TipTap/ProseMirror contenteditable editor', async () => {
    document.body.innerHTML = `
      <div class="flex min-h-24 items-stretch">
        <div class="w-full text-16">
          <div contenteditable="true" role="textbox" class="tiptap ProseMirror"></div>
        </div>
      </div>
    `
    const editor = document.querySelector<HTMLElement>('.tiptap.ProseMirror')!
    const inputListener = vi.fn()
    editor.addEventListener('input', inputListener)

    await createDoubaoAdapter({ inputTimeoutMs: 250, skillButtonTimeoutMs: 20 }).fillAndSend('你好 <doubao>', false)

    expect(editor.textContent).toBe('你好 <doubao>')
    expect(inputListener).toHaveBeenCalledTimes(1)
  })

  it('reads text-only replies without image syntax', () => {
    const textContent = document.createElement('div')
    textContent.setAttribute('class', 'message-content')
    textContent.textContent = '这是一段纯文字回复'
    const block = buildMessageBlock(textContent.outerHTML)
    attachListContainer(block)

    expect(createDoubaoAdapter().readResponseText(block)).toBe('这是一段纯文字回复')
  })

  it('reads image-only replies as image markdown', () => {
    const block = buildMessageBlock(`
      <div class="message-content"></div>
      <picture><img src="https://lf-flow-web-cdn.doubao.com/obj/img/1.png" alt="image"></picture>
    `)
    attachListContainer(block)

    expect(createDoubaoAdapter().readResponseText(block)).toBe('![image](https://lf-flow-web-cdn.doubao.com/obj/img/1.png)')
  })

  it('reads text and images from the same message block atomically', () => {
    const block = buildMessageBlock(`
      <div class="message-content"><p>这是配图说明</p></div>
      <picture><img src="https://lf-flow-web-cdn.doubao.com/obj/img/a.png" alt="image"></picture>
    `)
    attachListContainer(block)

    expect(createDoubaoAdapter().readResponseText(block)).toBe(
      '这是配图说明\n\n![image](https://lf-flow-web-cdn.doubao.com/obj/img/a.png)',
    )
  })

  it('does not leak images from a neighbouring message block', () => {
    const first = buildMessageBlock(`
      <div class="message-content"><p>旧回复</p></div>
      <picture><img src="https://lf-flow-web-cdn.doubao.com/obj/img/old.png" alt="image"></picture>
    `)
    const second = buildMessageBlock(`
      <div class="message-content"><p>新回复</p></div>
    `)
    attachListContainer(first, second)

    const secondBlock = document.querySelectorAll('[data-testid="message-block-container"]')[1] as HTMLElement
    expect(createDoubaoAdapter().readResponseText(secondBlock)).toBe('新回复')
  })

  it('scopes images to the reply message block even when other blocks have images', () => {
    const first = buildMessageBlock(`
      <div class="message-content"><p>旧的纯文</p></div>
    `)
    const second = buildMessageBlock(`
      <div class="message-content"><p>新消息带图文</p></div>
      <picture><img src="https://lf-flow-web-cdn.doubao.com/obj/img/new.png" alt="image"></picture>
    `)
    document.body.appendChild(first)
    document.body.appendChild(second)

    const secondBlock = document.querySelectorAll('[data-testid="message-block-container"]')[1] as HTMLElement
    expect(createDoubaoAdapter().readResponseText(secondBlock)).toBe(
      '新消息带图文\n\n![image](https://lf-flow-web-cdn.doubao.com/obj/img/new.png)',
    )
  })

  it('finds the response container for a deeply nested text node', () => {
    const block = buildMessageBlock(`
      <div class="message-content"><div><span><p>深层文字</p></span></div></div>
      <picture><img src="https://lf-flow-web-cdn.doubao.com/obj/img/deep.png" alt="image"></picture>
    `)
    attachListContainer(block)

    const textNode = block.querySelector('p')!
    const container = createDoubaoAdapter().findResponseContainer(textNode.closest('.message-content'))

    expect(container).toBe(block)
  })
})