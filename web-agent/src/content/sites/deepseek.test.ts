// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createDeepSeekAdapter } from './deepseek'

describe('DeepSeek site adapter', () => {
  it('extracts DeepSeek conversation ids and normalized safe urls', () => {
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com/a/chat/s/abc-123?model=deepseek-chat' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: 'abc-123',
      conversationUrl: 'https://chat.deepseek.com/a/chat/s/abc-123?model=deepseek-chat',
    })
  })

  it('extracts DeepSeek conversation ids on the migrated www.deepseek.com domain', () => {
    const adapter = createDeepSeekAdapter({ href: 'https://www.deepseek.com/a/chat/s/abc-123?model=deepseek-reasoner' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: 'abc-123',
      conversationUrl: 'https://www.deepseek.com/a/chat/s/abc-123?model=deepseek-reasoner',
    })
  })

  it('does not report non-DeepSeek urls', () => {
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com.evil.example/a/chat/s/abc-123' })

    expect(adapter.getConversationSnapshot()).toEqual({
      conversationId: undefined,
      conversationUrl: undefined,
    })
  })

  it('writes prompt text into the new hero textarea on www.deepseek.com', async () => {
    document.body.innerHTML = `
      <div class="ds-glass-card ds-input-wrapper">
        <textarea class="hero-input" placeholder="问点什么，一起探索"></textarea>
      </div>
    `
    const editor = document.querySelector<HTMLTextAreaElement>('textarea.hero-input')!
    const inputListener = vi.fn()
    editor.addEventListener('input', inputListener)

    await createDeepSeekAdapter({ inputTimeoutMs: 250 }).fillAndSend('你好世界', false)

    expect(editor.value).toBe('你好世界')
    expect(inputListener).toHaveBeenCalledTimes(1)
  })

  it('clicks the generic icon-only send button in the new composer layout', async () => {
    document.body.innerHTML = `
      <div class="ds-glass-card ds-input-wrapper">
        <textarea class="hero-input" placeholder="问点什么，一起探索"></textarea>
        <button class="ds-hero-toggle-btn">深度思考</button>
        <button class="flex-shrink-0 w-9 h-9 rounded-full"><svg><path d="M0 0"/></svg></button>
      </div>
    `
    const sendButton = document.querySelector<HTMLElement>('.rounded-full')!
    const clickListener = vi.fn()
    sendButton.addEventListener('click', clickListener)

    await createDeepSeekAdapter({ inputTimeoutMs: 250 }).fillAndSend('hello', true)

    expect(clickListener).toHaveBeenCalledTimes(1)
  })

  it('writes prompt text into the DeepSeek textarea composer', async () => {
    document.body.innerHTML = `
      <div class="aaff8b8f">
        <textarea name="search" placeholder="给 DeepSeek 发送消息 "></textarea>
      </div>
    `
    const editor = document.querySelector<HTMLTextAreaElement>('textarea[name="search"]')!
    const inputListener = vi.fn()
    editor.addEventListener('input', inputListener)

    await createDeepSeekAdapter().fillAndSend('你好 <deepseek>', false)

    expect(editor.value).toBe('你好 <deepseek>')
    expect(inputListener).toHaveBeenCalledTimes(1)
  })

  it('clicks the enabled DeepSeek send button near the composer', async () => {
    document.body.innerHTML = `
      <div class="aaff8b8f">
        <textarea name="search" placeholder="给 DeepSeek 发送消息 "></textarea>
        <div class="ec4f5d61">
          <div role="button" aria-disabled="false" class="ds-toggle-button">深度思考</div>
          <div class="bf38813a">
            <div role="button" aria-disabled="false" class="ds-icon-button _52c986b" tabindex="0"></div>
          </div>
        </div>
      </div>
    `
    const sendButton = document.querySelector<HTMLElement>('.bf38813a [role="button"]')!
    const clickListener = vi.fn()
    sendButton.addEventListener('click', clickListener)

    await createDeepSeekAdapter({ inputTimeoutMs: 250 }).fillAndSend('hello', true)

    expect(clickListener).toHaveBeenCalledTimes(1)
  })

  it('reads only final assistant markdown replies and skips thinking content', () => {
    document.body.innerHTML = `
      <div class="_4f9bf79 d7dc56a8 _43c05b" data-virtual-list-item-key="2">
        <div class="ds-message _63c77b1">
          <div class="_74c0879">
            <div class="e1675d8b ds-think-content _767406f">
              <div class="ds-markdown"><p>内部思考</p></div>
            </div>
          </div>
          <div class="ds-markdown">
            <p class="ds-markdown-paragraph"><span>你好！很高兴见到你。</span></p>
          </div>
        </div>
      </div>
    `

    expect(createDeepSeekAdapter().getAllAssistantReplies()).toEqual(['你好！很高兴见到你。'])
  })

  it('converts DeepSeek reply DOM to markdown', () => {
    document.body.innerHTML = `
      <div class="_4f9bf79" data-virtual-list-item-key="2">
        <div class="ds-message">
          <div class="ds-markdown">
            <h2>方案</h2>
            <p><strong>结论</strong>：可以做</p>
            <ul><li>先接入 adapter</li><li>再验证 iframe</li></ul>
          </div>
        </div>
      </div>
    `
    const response = document.querySelector('.ds-markdown')!

    expect(createDeepSeekAdapter().readResponseMarkdown?.(response)).toBe('## 方案\n\n**结论**：可以做\n\n- 先接入 adapter\n- 再验证 iframe')
  })

  it('reports unauthorized on a DeepSeek login path', () => {
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com/auth/login' })

    expect(adapter.checkStatus?.()).toMatchObject({ status: 'unauthorized' })
  })

  it('reports unauthorized when only a login prompt is visible', () => {
    document.body.innerHTML = '<button>登录</button>'
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com/' })

    expect(adapter.checkStatus?.()).toMatchObject({ status: 'unauthorized' })
    document.body.innerHTML = ''
  })

  it('reports ready when a DeepSeek editor is present', () => {
    document.body.innerHTML = '<textarea class="hero-input" placeholder="问点什么"></textarea>'
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com/' })

    expect(adapter.checkStatus?.()).toMatchObject({ status: 'ready' })
    document.body.innerHTML = ''
  })

  it('fillAndSend fails fast with SITE_LOGIN_REQUIRED instead of waiting for a missing editor', async () => {
    document.body.innerHTML = '<button>登录</button>'
    const adapter = createDeepSeekAdapter({ href: 'https://chat.deepseek.com/', inputTimeoutMs: 500 })

    await expect(adapter.fillAndSend('hello')).rejects.toThrow('SITE_LOGIN_REQUIRED')
    document.body.innerHTML = ''
  })
})
