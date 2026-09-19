import type { WebAgentStore } from '../group/types'
import type { WebAgentControlConnectionState, WebAgentControlConnectionStatus } from '../shared/localControlProtocol'

export function agentControlStatusState(store: WebAgentStore, status: WebAgentControlConnectionStatus): WebAgentControlConnectionState {
  if (!store.settings.agentControlEnabled) return 'disabled'
  if (status.port === store.settings.agentControlPort && status.state !== 'disabled') return status.state
  return 'connecting'
}

export function agentControlStatusText(store: WebAgentStore, status: WebAgentControlConnectionStatus): string {
  const port = store.settings.agentControlPort
  const state = agentControlStatusState(store, status)
  if (state === 'disabled') return `端口 ${port}，仅允许本机连接。开启后本机工具可创建群聊并发送任务。`
  if (state === 'connected') return `已连接 Web Agent CLI daemon（端口 ${port}）。本机工具可以创建群聊并发送任务。`
  if (state === 'connecting') return `正在连接 Web Agent CLI daemon（端口 ${port}）。`
  return `未连接 Web Agent CLI daemon（端口 ${port}）。请安装 Web Agent CLI，或运行 web-agent daemon start 启动守护进程。`
}
