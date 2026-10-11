import type { WebAgentStore } from '../group/types'
import type { WebAgentControlConnectionStatus } from '../shared/localControlProtocol'

const els = {
  controlStatus: document.getElementById('control-status'),
  deepseekStatus: document.getElementById('deepseek-status'),
  doubaoStatus: document.getElementById('doubao-status'),
  refreshStatus: document.getElementById('btn-refresh-status'),
  openDeepseek: document.getElementById('btn-open-deepseek'),
  openDoubao: document.getElementById('btn-open-doubao'),
  teamPage: document.getElementById('btn-open-team'),
  recentTasks: document.getElementById('recent-tasks'),
}

let store: WebAgentStore | null = null
let controlStatus: WebAgentControlConnectionStatus | undefined

function render(): void {
  const chip = els.controlStatus
  const state = controlStatus?.state ?? 'connecting'
  if (!chip) return
  chip.className = 'wb-status-chip'
  if (state === 'connected') chip.classList.add('connected')
  if (state === 'disconnected' || state === 'disabled') chip.classList.add('disconnected')
  chip.textContent = state === 'connected'
    ? `本地控制已连接 · 端口 ${controlStatus?.port ?? '-'}`
    : state === 'connecting'
      ? '本地控制连接中…'
      : '本地控制未连接'
}

function renderGatewayStatus(): void {
  const defaultSite = store?.settings?.defaultChatSite
  if (els.deepseekStatus) {
    els.deepseekStatus.textContent = defaultSite === 'deepseek'
      ? '默认站点（已启用）'
      : defaultSite === 'doubao'
        ? '可用（豆包为默认）'
        : '可用'
  }
  if (els.doubaoStatus) {
    els.doubaoStatus.textContent = defaultSite === 'doubao'
      ? '默认站点（已启用）'
      : defaultSite === 'deepseek'
        ? '可用（DeepSeek 为默认）'
        : '可用'
  }
}

function renderRecentTasks(): void {
  const el = els.recentTasks
  if (!el || !store) return
  const chats = (store.chatOrder ?? []).map(id => store?.chatsById[id]).filter(Boolean)
  if (chats.length === 0) {
    el.textContent = '暂无群聊记录'
    return
  }
  const latest = chats.slice(-3).reverse()
  el.innerHTML = latest.map(chat => {
    const messageCount = chat?.messageIds?.length ?? 0
    const created = chat?.createdAt ? new Date(chat.createdAt).toLocaleString() : ''
    return `<div class="wb-task-row"><span class="wb-task-name">${escapeHtml(chat?.name || '未命名')}</span><span class="wb-task-meta">${messageCount} 条消息 · ${escapeHtml(created)}</span></div>`
  }).join('')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

async function refreshState(): Promise<void> {
  try {
    const response = await chrome.runtime.sendMessage({ type: 'GROUP_STORE_GET' }) as { ok?: boolean; store?: WebAgentStore; controlStatus?: WebAgentControlConnectionStatus; error?: string }
    if (response?.ok === false) throw new Error(response.error || '读取状态失败')
    store = response?.store ?? null
    controlStatus = response?.controlStatus
    render()
    renderGatewayStatus()
    renderRecentTasks()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (els.controlStatus) {
      els.controlStatus.textContent = `读取失败：${message}`
      els.controlStatus.className = 'wb-status-chip disconnected'
    }
  }
}

function navigate(target: string): void {
  chrome.runtime.sendMessage({ type: 'WEB_AGENT_WORKBENCH_NAVIGATE', target })
    .catch(error => {
      if (els.controlStatus) els.controlStatus.textContent = `导航失败：${error instanceof Error ? error.message : String(error)}`
    })
}

els.refreshStatus?.addEventListener('click', () => {
  void refreshState()
})
els.openDeepseek?.addEventListener('click', () => navigate('focus-deepseek'))
els.openDoubao?.addEventListener('click', () => navigate('focus-doubao'))
els.teamPage?.addEventListener('click', () => navigate('team'))

chrome.runtime.onMessage.addListener((message: unknown) => {
  if (!message || typeof message !== 'object') return
  const payload = message as { type?: string; controlStatus?: WebAgentControlConnectionStatus; store?: WebAgentStore }
  if (payload.type === 'GROUP_CONTROL_STATUS_UPDATED') {
    controlStatus = payload.controlStatus
    render()
  }
  if (payload.type === 'GROUP_STORE_UPDATED') {
    store = payload.store ?? store
    renderGatewayStatus()
    renderRecentTasks()
  }
  return false
})

render()
void refreshState()