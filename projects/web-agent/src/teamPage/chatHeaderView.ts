import type { GroupChat, GroupMessage, GroupRole, RoomMode } from '../group/types'
import { normalizeLanguage, translateUi, type TeamLanguage } from '../shared/i18n'
import type { TeamPageState } from './appState'

export interface ChatHeaderViewDependencies {
  state: TeamPageState
  chatTitleEl: HTMLElement
  chatSubtitleEl: HTMLElement
  chatStatusEl: HTMLElement
  togglePeopleDrawerEl: HTMLButtonElement
  openOrchestrationEl: HTMLButtonElement
  getLanguage(): TeamLanguage
  getCurrentChat(): GroupChat | undefined
  getCurrentRoles(): GroupRole[]
  getCurrentMessages(): GroupMessage[]
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
}

export interface ChatHeaderView {
  renderChatHeader(): void
}

export function createChatHeaderView(deps: ChatHeaderViewDependencies): ChatHeaderView {
  let manualMentionToggle: HTMLButtonElement | undefined

  function renderChatHeader(): void {
    const chat = deps.getCurrentChat()
    const roles = deps.getCurrentRoles()
    const messages = deps.getCurrentMessages()
    if (!chat) {
      deps.chatTitleEl.textContent = ui('未选择群聊')
      deps.chatSubtitleEl.textContent = ui('创建或选择一个群聊开始协作')
      deps.chatStatusEl.className = 'status-pill'
      deps.chatStatusEl.textContent = ui('空')
      deps.togglePeopleDrawerEl.textContent = ui('成员 0')
      deps.togglePeopleDrawerEl.disabled = true
      deps.openOrchestrationEl.hidden = true
      if (manualMentionToggle) {
        manualMentionToggle.hidden = true
        manualMentionToggle.disabled = true
      }
      return
    }

    deps.chatTitleEl.textContent = chat.name
    deps.chatSubtitleEl.textContent = roles.length ? ui(`${modeLabel(chat.mode)} · ${roles.length} 位成员 · ${messages.length} 条消息`) : ui('暂无成员')
    deps.chatStatusEl.className = `status-pill status-${chat.status}`
    deps.chatStatusEl.textContent = ui(chatStatusLabel(chat.status))
    deps.togglePeopleDrawerEl.disabled = false
    deps.togglePeopleDrawerEl.textContent = ui(`成员 ${roles.length}`)
    deps.togglePeopleDrawerEl.setAttribute('aria-label', ui(deps.state.peopleDrawerOpen ? '收起成员面板' : '打开成员面板'))
    deps.togglePeopleDrawerEl.setAttribute('aria-expanded', String(deps.state.peopleDrawerOpen))
    deps.openOrchestrationEl.hidden = chat.mode !== 'collaborative'

    if (!manualMentionToggle) {
      manualMentionToggle = document.createElement('button')
      manualMentionToggle.type = 'button'
      manualMentionToggle.className = 'btn drawer-summary manual-mention-toggle'
      manualMentionToggle.setAttribute('role', 'switch')
      manualMentionToggle.addEventListener('click', async () => {
        const current = deps.getCurrentChat()
        if (!current) return
        const plainMessagesReplyAll = current.requireManualMention === false
        await deps.runCommand('GROUP_CHAT_UPDATE', { chatId: current.id, requireManualMention: plainMessagesReplyAll ? true : false })
      })
      deps.togglePeopleDrawerEl.parentElement?.insertBefore(manualMentionToggle, deps.togglePeopleDrawerEl)
    }
    manualMentionToggle.hidden = chat.mode !== 'collaborative'
    manualMentionToggle.disabled = false
    const plainMessagesReplyAll = chat.requireManualMention === false
    const ruleHint = '开启后，普通消息也会触发所有成员回复；关闭后，只有 @ 成员或 @所有人才触发回复'
    manualMentionToggle.textContent = ui('免@')
    manualMentionToggle.title = ui(ruleHint)
    manualMentionToggle.setAttribute('aria-label', ui(ruleHint))
    manualMentionToggle.setAttribute('aria-pressed', String(plainMessagesReplyAll))
    manualMentionToggle.setAttribute('aria-checked', String(plainMessagesReplyAll))
  }

  function ui(source: string): string {
    return translateUi(source, normalizeLanguage(deps.getLanguage()))
  }

  return { renderChatHeader }
}

function modeLabel(mode: RoomMode): string {
  return mode === 'collaborative' ? '协作群聊模式' : '独立专家模式'
}

function chatStatusLabel(status: GroupChat['status']): string {
  const labels: Record<GroupChat['status'], string> = {
    draft: '草稿',
    initializing: '初始化中',
    ready: '进行中',
    running: '运行中',
    error: '异常',
  }
  return labels[status]
}
