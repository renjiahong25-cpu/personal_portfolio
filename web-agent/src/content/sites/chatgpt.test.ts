// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createChatGptAdapter } from './chatgpt'

describe('ChatGPT site adapter', () => {
  it('extracts ChatGPT conversation ids and normalized safe urls', () => {
    const adapter = createChatGptAdapter({ href: 'https://chatgpt.com/c/abc-123?model=gpt-5' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: 'abc-123',
      conversationUrl: 'https://chatgpt.com/c/abc-123?model=gpt-5',
    })
  })

  it('extracts conversation ids from ChatGPT GPTs conversation urls', () => {
    const adapter = createChatGptAdapter({ href: 'https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian/c/abc-123?model=gpt-5' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: 'abc-123',
      conversationUrl: 'https://chatgpt.com/g/g-LrdzaEiqT-fei-fei-jiao-lian/c/abc-123?model=gpt-5',
    })
  })

  it('does not report non-ChatGPT urls', () => {
    const adapter = createChatGptAdapter({ href: 'https://chatgpt.com.evil.example/c/abc-123' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: undefined,
      conversationUrl: undefined,
    })
  })

  it('writes prompt text into the ProseMirror composer', async () => {
    document.body.innerHTML = `
      <form data-type="unified-composer">
        <div contenteditable="true" class="ProseMirror" id="prompt-textarea" role="textbox" aria-label="与 ChatGPT 聊天">
          <p data-placeholder="有问题，尽管问" class="placeholder"><br></p>
        </div>
      </form>
    `
    const editor = document.querySelector<HTMLElement>('#prompt-textarea')!
    const inputListener = vi.fn()
    editor.addEventListener('input', inputListener)

    await createChatGptAdapter().fillAndSend('你好 <test>', false)

    expect(editor.textContent).toBe('你好 <test>')
    expect(editor.querySelector('test')).toBeNull()
    expect(inputListener).toHaveBeenCalledTimes(1)
  })

  it('waits for the ChatGPT send button before clicking', async () => {
    document.body.innerHTML = `
      <form data-type="unified-composer">
        <div contenteditable="true" class="ProseMirror" id="prompt-textarea" role="textbox"></div>
        <button type="button" aria-label="启动语音功能" disabled>Voice</button>
      </form>
    `
    const voiceButton = document.querySelector<HTMLButtonElement>('button')!
    const clickListener = vi.fn()
    voiceButton.addEventListener('click', clickListener)
    window.setTimeout(() => {
      voiceButton.disabled = false
      voiceButton.setAttribute('aria-label', '发送提示')
    }, 20)

    await createChatGptAdapter({ inputTimeoutMs: 250 }).fillAndSend('hello', true)

    expect(clickListener).toHaveBeenCalledTimes(1)
  })

  it('reads assistant markdown replies without action button text', () => {
    document.body.innerHTML = `
      <section data-turn="assistant" data-testid="conversation-turn-2">
        <div data-message-author-role="assistant" data-message-id="reply-1">
          <div class="markdown">
            <p>你好！今天想聊点什么？</p>
            <ul><li><p>调研</p></li><li><p>写代码</p></li></ul>
          </div>
        </div>
        <div aria-label="回复操作"><button aria-label="复制回复">复制回复</button></div>
      </section>
    `

    expect(createChatGptAdapter().getAllAssistantReplies()).toEqual(['你好！今天想聊点什么？\n\n调研\n\n写代码'])
  })

  it('uses the ChatGPT copy action to read markdown replies and restores the clipboard', async () => {
    let clipboardText = '用户原来的剪贴板'
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        readText: vi.fn(async () => clipboardText),
        writeText: vi.fn(async (text: string) => {
          clipboardText = text
        }),
      },
    })
    document.body.innerHTML = `
      <section data-turn="assistant" data-testid="conversation-turn-2">
        <div data-message-author-role="assistant" data-message-id="reply-1">
          <div class="markdown">
            <p>标题</p>
            <pre><code>const answer = 42</code></pre>
          </div>
        </div>
        <div aria-label="回复操作">
          <button aria-label="复制回复" data-testid="copy-turn-action-button">复制</button>
        </div>
      </section>
    `
    document.querySelector<HTMLButtonElement>('[data-testid="copy-turn-action-button"]')?.addEventListener('click', () => {
      clipboardText = '标题\n\n```ts\nconst answer = 42\n```'
    })
    const response = document.querySelector('[data-message-author-role="assistant"]')!

    const copied = await createChatGptAdapter({ clipboardPollMs: 5, clipboardTimeoutMs: 50 }).readResponseTextFromCopy?.(response)

    expect(copied).toBe('标题\n\n```ts\nconst answer = 42\n```')
    expect(clipboardText).toBe('用户原来的剪贴板')
  })

  it('uses the turn copy action instead of code block copy buttons', async () => {
    let clipboardText = '用户原来的剪贴板'
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        readText: vi.fn(async () => clipboardText),
        writeText: vi.fn(async (text: string) => {
          clipboardText = text
        }),
      },
    })
    document.body.innerHTML = `
      <section data-turn="assistant" data-testid="conversation-turn-4">
        <div data-message-author-role="assistant" data-message-id="reply-1" data-turn-start-message="true">
          <div class="markdown">
            <p>完整回复开头</p>
            <pre>
              <button aria-label="复制">复制代码</button>
              <code>const partial = true</code>
            </pre>
            <p>完整回复结尾</p>
          </div>
        </div>
        <div aria-label="回复操作" role="group">
          <button aria-label="复制回复" data-testid="copy-turn-action-button">复制回复</button>
        </div>
      </section>
    `
    document.querySelector<HTMLButtonElement>('pre button[aria-label="复制"]')?.addEventListener('click', () => {
      clipboardText = 'const partial = true'
    })
    document.querySelector<HTMLButtonElement>('[data-testid="copy-turn-action-button"]')?.addEventListener('click', () => {
      clipboardText = '完整回复开头\n\n```ts\nconst partial = true\n```\n\n完整回复结尾'
    })
    const response = document.querySelector('[data-message-author-role="assistant"]')!

    const copied = await createChatGptAdapter({ clipboardPollMs: 5, clipboardTimeoutMs: 50 }).readResponseTextFromCopy?.(response)

    expect(copied).toBe('完整回复开头\n\n```ts\nconst partial = true\n```\n\n完整回复结尾')
    expect(clipboardText).toBe('用户原来的剪贴板')
  })

  it('converts ChatGPT reply DOM to markdown when copy output is unavailable', () => {
    document.body.innerHTML = `
      <div data-message-author-role="assistant">
        <div class="markdown">
          <h2>方案</h2>
          <p><strong>结论</strong>：可以做</p>
          <ul><li>先做复制</li><li>再做兜底</li></ul>
          <pre><code>const ok = true</code></pre>
        </div>
      </div>
    `
    const response = document.querySelector('[data-message-author-role="assistant"]')!

    expect(createChatGptAdapter().readResponseMarkdown?.(response)).toBe('## 方案\n\n**结论**：可以做\n\n- 先做复制\n- 再做兜底\n\n```\nconst ok = true\n```')
  })

  it('treats ChatGPT streaming busy indicators as generating even without a visible stop button', () => {
    document.body.innerHTML = `
      <section data-turn="assistant" data-testid="conversation-turn-2">
        <div data-message-author-role="assistant" data-message-id="reply-1">
          <div class="markdown"><p>先输出的一段内容</p></div>
          <div class="result-streaming pulse" aria-busy="true"></div>
        </div>
      </section>
    `

    expect(createChatGptAdapter().isGenerating()).toBe(true)
  })
})
