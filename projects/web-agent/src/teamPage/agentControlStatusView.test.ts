import { describe, expect, it } from 'vitest'
import { createDefaultStore } from '../group/store'
import { agentControlStatusText } from './agentControlStatusView'

describe('agent control status view', () => {
  it('prompts users to install the CLI or start the daemon when enabled but disconnected', () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true

    expect(agentControlStatusText(store, {
      state: 'disconnected',
      port: 19305,
      lastError: 'Web Agent CLI daemon is not reachable.',
    })).toBe('未连接 Web Agent CLI daemon（端口 19305）。请安装 Web Agent CLI，或运行 web-agent daemon start 启动守护进程。')
  })

  it('shows a connected status when the extension has joined the local daemon', () => {
    const store = createDefaultStore()
    store.settings.agentControlEnabled = true

    expect(agentControlStatusText(store, { state: 'connected', port: 19305 })).toBe('已连接 Web Agent CLI daemon（端口 19305）。本机工具可以创建群聊并发送任务。')
  })

  it('keeps the original explanatory copy while local agent control is off', () => {
    const store = createDefaultStore()

    expect(agentControlStatusText(store, { state: 'disabled', port: 19305 })).toBe('端口 19305，仅允许本机连接。开启后本机工具可创建群聊并发送任务。')
  })
})
