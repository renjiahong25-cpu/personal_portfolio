// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { applyTeamLanguage, createLanguageSettingsController } from './languageController'

describe('team page language controller', () => {
  it('renders English by default and can restore Chinese copy', () => {
    document.body.innerHTML = `
      <button id="settings-button" aria-label="设置" data-tooltip="设置">⚙</button>
      <button id="agent-control-toggle">本机智能体控制：关闭</button>
      <p id="agent-control-status">端口 19305，仅允许本机连接。开启后本机工具可创建群聊并发送任务。</p>
      <textarea id="message-input" placeholder="输入消息，@成员可指定回复；不 @ 仅记录到群聊。"></textarea>
    `

    applyTeamLanguage('en')

    expect(document.documentElement.lang).toBe('en')
    expect(document.querySelector<HTMLButtonElement>('#settings-button')?.ariaLabel).toBe('Settings')
    expect(document.querySelector<HTMLButtonElement>('#agent-control-toggle')?.textContent).toBe('Local agent control: Off')
    expect(document.querySelector<HTMLTextAreaElement>('#message-input')?.placeholder).toBe('Type a message. Mention @members to request replies; without @ it is saved to the chat.')

    applyTeamLanguage('zh-CN')

    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.querySelector<HTMLButtonElement>('#settings-button')?.ariaLabel).toBe('设置')
    expect(document.querySelector<HTMLButtonElement>('#agent-control-toggle')?.textContent).toBe('本机智能体控制：关闭')
  })

  it('persists language changes from the settings switch', () => {
    document.body.innerHTML = `
      <button id="language-en" aria-pressed="false">English</button>
      <button id="language-zh" aria-pressed="true">中文</button>
    `
    const runCommand = vi.fn(async () => undefined)
    const controller = createLanguageSettingsController({
      englishButton: document.querySelector<HTMLButtonElement>('#language-en')!,
      chineseButton: document.querySelector<HTMLButtonElement>('#language-zh')!,
      getLanguage: () => 'zh-CN',
      runCommand,
      showError: vi.fn(),
    })

    controller.render()
    controller.registerEvents()
    document.querySelector<HTMLButtonElement>('#language-en')!.click()

    expect(runCommand).toHaveBeenCalledWith('GROUP_SETTINGS_UPDATE', { language: 'en' })
  })

  it('applies the selected language immediately while the setting is being saved', () => {
    document.body.innerHTML = `
      <button id="language-en" aria-pressed="true">English</button>
      <button id="language-zh" aria-pressed="false">中文</button>
      <button id="agent-control-toggle">本机智能体控制：关闭</button>
    `
    const runCommand = vi.fn(() => new Promise<void>(() => undefined))
    const controller = createLanguageSettingsController({
      englishButton: document.querySelector<HTMLButtonElement>('#language-en')!,
      chineseButton: document.querySelector<HTMLButtonElement>('#language-zh')!,
      getLanguage: () => 'en',
      runCommand,
      showError: vi.fn(),
    })

    controller.render()
    expect(document.querySelector<HTMLButtonElement>('#agent-control-toggle')?.textContent).toBe('Local agent control: Off')

    controller.registerEvents()
    document.querySelector<HTMLButtonElement>('#language-zh')!.click()

    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.querySelector<HTMLButtonElement>('#language-zh')?.getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelector<HTMLButtonElement>('#agent-control-toggle')?.textContent).toBe('本机智能体控制：关闭')
    expect(runCommand).toHaveBeenCalledWith('GROUP_SETTINGS_UPDATE', { language: 'zh-CN' })
  })

  it('translates dynamically rendered role panel status copy', () => {
    document.body.innerHTML = `
      <aside>
        <span>尚未读取消息</span>
        <span>等待连接</span>
        <span>已读 3 条</span>
        <span>网页已连接</span>
        <span>连接 API</span>
        <span>在线</span>
        <span>进行中</span>
        <span>协作群聊模式 · 2 位成员 · 3 条消息</span>
      </aside>
    `

    applyTeamLanguage('en')

    expect(document.body.textContent).toContain('No messages read yet')
    expect(document.body.textContent).toContain('Waiting to connect')
    expect(document.body.textContent).toContain('Read 3 messages')
    expect(document.body.textContent).toContain('Web connected')
    expect(document.body.textContent).toContain('API connected')
    expect(document.body.textContent).toContain('Online')
    expect(document.body.textContent).toContain('Active')
    expect(document.body.textContent).toContain('Collaborative mode · 2 members · 3 messages')

    applyTeamLanguage('zh-CN')

    expect(document.body.textContent).toContain('尚未读取消息')
    expect(document.body.textContent).toContain('等待连接')
  })

  it('translates message controls without translating message bodies', () => {
    document.body.innerHTML = `
      <section id="messages">
        <article>
          <div class="message-body">进行中</div>
          <div class="message-tools">
            <button aria-label="重新回复">↻</button>
          </div>
        </article>
      </section>
    `

    applyTeamLanguage('en')

    expect(document.querySelector('.message-body')?.textContent).toBe('进行中')
    expect(document.querySelector<HTMLButtonElement>('.message-tools button')?.ariaLabel).toBe('Retry reply')
  })
})
