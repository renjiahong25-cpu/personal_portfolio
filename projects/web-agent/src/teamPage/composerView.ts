import { defaultMentionTargetForMessage, parseGroupMentions, roleMentionLabel, roleMentionLabelOptionsFromSettings, roleModelLabel } from '../group/mentionParser'
import type { GroupChat, GroupMessage, GroupRole, MessageReference, WebAgentStore } from '../group/types'
import type { TeamPageState } from './appState'
import { getVisibleThinkingRoles, shouldAutoReconnectRole, shouldConfirmMentionWithEnter, shouldSendMessageWithEnter } from './chatExperience'
import { runCommandWithReconnect } from './sendWithReconnect'

export interface ComposerViewDependencies {
  state: TeamPageState
  composerFormEl: HTMLFormElement
  targetPreviewEl: HTMLElement
  busyPreviewEl: HTMLElement
  sendButtonEl: HTMLButtonElement
  messageInputEl: HTMLTextAreaElement
  referenceDraftEl: HTMLElement
  mentionPanelEl: HTMLElement
  getStore(): WebAgentStore
  getCurrentChat(): GroupChat | undefined
  getCurrentRoles(): GroupRole[]
  roleToneClass(seed: string | undefined): string
  roleAvatarLabel(name: string | undefined): string
  reconnectRolesForSend(chat: GroupChat, roles: GroupRole[]): Promise<void>
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
  showError(message: string): void
}

export interface ComposerView {
  renderComposerState(): void
  registerComposerEvents(): void
  insertMention(role: GroupRole): void
  setReference(message: GroupMessage): void
  submitComposerMessage(): Promise<void>
}

type MentionOption =
  | { type: 'all' }
  | { type: 'role'; role: GroupRole }

export function createComposerView(deps: ComposerViewDependencies): ComposerView {
  function mentionLabelOptions() {
    return mentionLabelOptionsFromStore(deps.getStore())
  }

  function roleDisplayName(role: GroupRole): string {
    return roleMentionLabel(role, mentionLabelOptions())
  }

  function renderComposerState(): void {
    renderReferenceDraft()
    renderMentionPanel()

    const chat = deps.getCurrentChat()
    const roles = deps.getCurrentRoles()
    const raw = deps.messageInputEl.value.trim()
    const parsed = parseGroupMentions(raw || 'x', roles, { ...mentionLabelOptions(), defaultTarget: chat ? defaultMentionTargetForMessage(raw, chat) : 'none' })
    const targetRoleIds = raw && parsed.ok ? parsed.targetRoleIds : []
    const targets = roles.filter(role => targetRoleIds.includes(role.id))
    const unavailable = targets.filter(role => role.status !== 'ready')
    const reconnecting = targets.filter(role => deps.state.reconnectingRoleKeys.has(teamRoleKey(role.chatId, role.id)))
    const thinking = getVisibleThinkingRoles(roles)

    if (!chat) {
      deps.targetPreviewEl.textContent = '选择群聊后可发送'
      deps.sendButtonEl.disabled = true
    } else if (roles.length === 0) {
      deps.targetPreviewEl.textContent = '当前群聊还没有人员'
      deps.sendButtonEl.disabled = true
    } else if (!raw) {
      deps.targetPreviewEl.textContent = defaultMentionTargetForMessage('', chat) === 'all'
        ? '输入消息；不 @ 会让所有成员回复，也可 @ 指定成员'
        : '输入消息；不 @ 仅记录，@ 人员触发回复'
      deps.sendButtonEl.disabled = true
    } else if (!parsed.ok) {
      deps.targetPreviewEl.textContent = parsed.error
      deps.sendButtonEl.disabled = true
    } else if (targets.length === 0) {
      deps.targetPreviewEl.textContent = '将作为群消息记录，不触发 AI；@ 人员可触发回复'
      deps.sendButtonEl.disabled = false
    } else if (reconnecting.length > 0) {
      const readyTargets = targets.filter(role => !reconnecting.includes(role) && role.status === 'ready')
      deps.targetPreviewEl.textContent = readyTargets.length > 0
        ? `将发送给：${readyTargets.map(roleDisplayName).join('、')}；正在连接：${reconnecting.map(roleDisplayName).join('、')}`
        : `正在自动连接：${reconnecting.map(roleDisplayName).join('、')}`
      deps.sendButtonEl.disabled = readyTargets.length === 0
    } else if (unavailable.length > 0) {
      const waiting = unavailable.filter(role => !shouldAutoReconnectRole(role))
      const readyTargets = targets.filter(role => role.status === 'ready')
      if (waiting.length > 0 && readyTargets.length === 0) {
        deps.targetPreviewEl.textContent = `请稍等：${waiting.map(roleDisplayName).join('、')} 正在回复`
        deps.sendButtonEl.disabled = true
      } else if (waiting.length > 0) {
        deps.targetPreviewEl.textContent = `将发送给：${readyTargets.map(roleDisplayName).join('、')}；跳过正在回复：${waiting.map(roleDisplayName).join('、')}`
        deps.sendButtonEl.disabled = false
      } else {
        deps.targetPreviewEl.textContent = `将先自动连接：${unavailable.map(roleDisplayName).join('、')}`
        deps.sendButtonEl.disabled = false
      }
    } else {
      deps.targetPreviewEl.textContent = `将发送给：${targets.map(roleDisplayName).join('、')}`
      deps.sendButtonEl.disabled = false
    }

    deps.busyPreviewEl.textContent = thinking.length > 0 ? `正在回复：${thinking.map(roleDisplayName).join('、')}` : ''
  }

  function renderReferenceDraft(): void {
    deps.referenceDraftEl.replaceChildren()
    if (!deps.state.selectedReference) {
      deps.referenceDraftEl.hidden = true
      return
    }

    deps.referenceDraftEl.hidden = false
    const preview = document.createElement('div')
    preview.className = 'reference-draft-preview'
    preview.textContent = `引用 ${deps.state.selectedReference.roleName || '人员'}：${deps.state.selectedReference.contentSnapshot}`

    const cancel = document.createElement('button')
    cancel.type = 'button'
    cancel.className = 'btn btn-ghost'
    cancel.setAttribute('aria-label', '取消引用')
    cancel.textContent = '×'
    cancel.addEventListener('click', () => {
      deps.state.selectedReference = undefined
      renderComposerState()
    })
    deps.referenceDraftEl.append(preview, cancel)
  }

  function renderMentionPanel(): void {
    const roles = deps.getCurrentRoles()
    const show = shouldShowMentionPanel(deps.messageInputEl.value) && roles.length > 0
    const options = createMentionOptions(roles)
    deps.state.mentionIndex = clampMentionIndex(deps.state.mentionIndex, options)
    deps.mentionPanelEl.hidden = !show
    deps.mentionPanelEl.replaceChildren()
    if (!show) return

    options.forEach((mentionOption, index) => {
      const option = document.createElement('button')
      option.type = 'button'
      option.className = `mention-option${index === deps.state.mentionIndex ? ' active' : ''}`
      const avatar = document.createElement('span')
      const name = document.createElement('span')
      name.className = 'mention-name'
      const site = document.createElement('span')
      site.className = 'mention-site-badge'
      if (mentionOption.type === 'all') {
        avatar.className = 'mention-avatar mention-avatar-all'
        avatar.textContent = '全'
        name.textContent = '所有人'
        site.textContent = '全员'
        option.addEventListener('click', () => insertAllMention())
      } else {
        const role = mentionOption.role
        avatar.className = `mention-avatar ${deps.roleToneClass(role.name)}`
        avatar.textContent = deps.roleAvatarLabel(role.name)
        name.textContent = role.name
        site.className = `mention-site-badge ${role.modelSource === 'external' ? 'site-pill-external' : `site-pill-${role.chatSite ?? 'gemini'}`}`
        site.textContent = roleModelLabel(role, mentionLabelOptions())
        option.addEventListener('click', () => insertMention(role))
      }
      option.append(avatar, name, site)
      deps.mentionPanelEl.append(option)
    })
  }

  function registerComposerEvents(): void {
    deps.composerFormEl.addEventListener('submit', event => {
      event.preventDefault()
      if (deps.sendButtonEl.disabled && deps.state.reconnectingRoleKeys.size > 0) return
      submitComposerMessage().catch(error => deps.showError(error instanceof Error ? error.message : String(error)))
    })

    deps.messageInputEl.addEventListener('input', () => {
      deps.state.mentionIndex = 0
      renderComposerState()
    })
    deps.messageInputEl.addEventListener('keyup', () => renderComposerState())
    deps.messageInputEl.addEventListener('keydown', event => {
      const roles = deps.getCurrentRoles()
      const mentionOptions = createMentionOptions(roles)
      const canHandleMention = roles.length > 0 && (shouldShowMentionPanel(deps.messageInputEl.value) || !deps.mentionPanelEl.hidden)
      if (canHandleMention) {
        deps.state.mentionIndex = clampMentionIndex(deps.state.mentionIndex, mentionOptions)
        if (event.key === 'ArrowDown') {
          event.preventDefault()
          deps.state.mentionIndex = (deps.state.mentionIndex + 1) % mentionOptions.length
          renderMentionPanel()
        } else if (event.key === 'ArrowUp') {
          event.preventDefault()
          deps.state.mentionIndex = (deps.state.mentionIndex - 1 + mentionOptions.length) % mentionOptions.length
          renderMentionPanel()
        } else if (shouldConfirmMentionWithEnter(event)) {
          event.preventDefault()
          const mentionOption = mentionOptions[deps.state.mentionIndex]
          if (mentionOption?.type === 'all') insertAllMention()
          if (mentionOption?.type === 'role') insertMention(mentionOption.role)
        } else if (event.key === 'Escape') {
          deps.mentionPanelEl.hidden = true
        }
        return
      }

      if (shouldSendMessageWithEnter(event)) {
        event.preventDefault()
        deps.composerFormEl.requestSubmit()
      }
    })
  }

  async function submitComposerMessage(): Promise<void> {
    const chat = deps.getCurrentChat()
    const raw = deps.messageInputEl.value.trim()
    if (!chat || !raw) return

    const targetResult = resolveMessageTargets(raw, deps.getCurrentRoles())
    if (!targetResult.ok) {
      deps.showError(targetResult.error)
      return
    }

    const waitingRoles = targetResult.roles.filter(role => role.status === 'thinking' && !shouldAutoReconnectRole(role))
    const readyRoles = targetResult.roles.filter(role => role.status === 'ready')
    if (waitingRoles.length > 0 && readyRoles.length === 0) {
      deps.showError(`请等待人员回复完成：${waitingRoles.map(roleDisplayName).join('、')}`)
      return
    }

    const reference = deps.state.selectedReference
    clearComposerAfterSend(raw, reference)
    try {
      await runCommandWithReconnect(deps, { chat, roles: targetResult.roles, type: 'GROUP_MESSAGE_SEND', payload: { chatId: chat.id, raw, reference } })
    } catch (error) {
      restoreComposerDraft(raw, reference)
      throw error
    }
  }

  function clearComposerAfterSend(raw: string, reference: MessageReference | undefined): void {
    if (deps.messageInputEl.value.trim() === raw) deps.messageInputEl.value = ''
    if (deps.state.selectedReference === reference) deps.state.selectedReference = undefined
    renderComposerState()
  }

  function restoreComposerDraft(raw: string, reference: MessageReference | undefined): void {
    if (!deps.messageInputEl.value.trim()) deps.messageInputEl.value = raw
    if (!deps.state.selectedReference) deps.state.selectedReference = reference
    renderComposerState()
  }

  function setReference(message: GroupMessage): void {
    deps.state.selectedReference = {
      messageId: message.id,
      roleId: message.roleId,
      roleName: message.roleName,
      contentSnapshot: message.content,
    }
    deps.messageInputEl.focus()
    renderComposerState()
  }

  function resolveMessageTargets(raw: string, roles: GroupRole[]): { ok: true; roles: GroupRole[] } | { ok: false; error: string } {
    const chat = deps.getCurrentChat()
    const parsed = parseGroupMentions(raw, roles, { ...mentionLabelOptions(), defaultTarget: chat ? defaultMentionTargetForMessage(raw, chat) : 'none' })
    if (!parsed.ok) return { ok: false, error: parsed.error }
    const targets = roles.filter(role => parsed.targetRoleIds.includes(role.id))
    return { ok: true, roles: targets }
  }

  function shouldShowMentionPanel(value: string): boolean {
    const cursor = deps.messageInputEl.selectionStart ?? value.length
    const beforeCursor = value.slice(0, cursor)
    const atIndex = beforeCursor.lastIndexOf('@')
    if (atIndex < 0) return false
    const mentionText = beforeCursor.slice(atIndex + 1)
    return !/\s/.test(mentionText)
  }

  function insertMention(role: GroupRole): void {
    insertMentionLabel(roleMentionLabel(role, mentionLabelOptions()))
  }

  function insertAllMention(): void {
    insertMentionLabel('所有人')
  }

  function insertMentionLabel(label: string): void {
    const value = deps.messageInputEl.value
    const cursor = deps.messageInputEl.selectionStart ?? value.length
    const beforeCursor = value.slice(0, cursor)
    const atIndex = beforeCursor.lastIndexOf('@')
    const rawPrefix = atIndex >= 0 ? value.slice(0, atIndex) : value.slice(0, cursor)
    const prefix = rawPrefix && !/\s$/.test(rawPrefix) ? `${rawPrefix} ` : rawPrefix
    const suffix = value.slice(cursor)
    const inserted = `${prefix}@${label} ${suffix}`
    deps.messageInputEl.value = inserted
    const nextCursor = prefix.length + label.length + 2
    deps.messageInputEl.setSelectionRange(nextCursor, nextCursor)
    deps.messageInputEl.focus()
    deps.mentionPanelEl.hidden = true
    renderComposerState()
  }

  return { renderComposerState, registerComposerEvents, insertMention, setReference, submitComposerMessage }
}

function createMentionOptions(roles: GroupRole[]): MentionOption[] {
  return [{ type: 'all' }, ...roles.map(role => ({ type: 'role' as const, role }))]
}

function clampMentionIndex(index: number, options: MentionOption[]): number {
  if (options.length === 0) return 0
  return Math.min(Math.max(0, index), options.length - 1)
}

function mentionLabelOptionsFromStore(store: WebAgentStore) {
  return roleMentionLabelOptionsFromSettings(store.settings)
}

function teamRoleKey(chatId: string, roleId: string): string {
  return `${chatId}:${roleId}`
}
