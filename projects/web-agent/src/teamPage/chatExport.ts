import type { GroupChat, GroupMessage, GroupRole, WebAgentStore, RoomMode } from '../group/types'

export function formatChatExportMarkdown(store: WebAgentStore, chat: GroupChat, exportedAt = new Date()): string {
  const roles = chat.roleIds
    .map(roleId => store.rolesById[roleId])
    .filter((role): role is GroupRole => Boolean(role))
  const messages = chat.messageIds
    .map(messageId => store.messagesById[messageId])
    .filter((message): message is GroupMessage => Boolean(message))
    .sort((left, right) => left.seq - right.seq || left.createdAt - right.createdAt)

  const lines = [
    `# ${chat.name}`,
    '',
    `- 导出时间：${formatExportDateTime(exportedAt)}`,
    `- 群聊模式：${modeLabel(chat.mode)}`,
    `- 当前成员：${roles.map(role => role.name).join('、') || '无'}`,
    `- 消息数量：${messages.length}`,
    '',
    '## 聊天记录',
    '',
  ]

  for (const message of messages) {
    lines.push(`### ${messageSenderLabel(store, message)}`)
    lines.push('')
    lines.push(`> ${formatExportDateTime(new Date(message.createdAt))}`)
    lines.push('')
    lines.push(message.content || '(空消息)')
    lines.push('')
  }

  return lines.join('\n').trimEnd() + '\n'
}

export function safeChatExportFilename(chatName: string, exportedAt = new Date()): string {
  const safeName = chatName
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    || 'group-chat'
  return `web-agent-${safeName}-${formatFilenameDateTime(exportedAt)}.md`
}

function messageSenderLabel(store: WebAgentStore, message: GroupMessage): string {
  if (message.type === 'user') return '我'
  if (message.type === 'system') return '系统'
  if (message.roleName) return message.roleName
  if (message.roleId) return store.rolesById[message.roleId]?.name ?? 'AI 人员'
  return 'AI 人员'
}

function modeLabel(mode: RoomMode): string {
  return mode === 'collaborative' ? '协作群聊' : '独立专家'
}

function formatExportDateTime(date: Date): string {
  const year = date.getFullYear()
  const month = pad(date.getMonth() + 1)
  const day = pad(date.getDate())
  const hour = pad(date.getHours())
  const minute = pad(date.getMinutes())
  const second = pad(date.getSeconds())
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

function formatFilenameDateTime(date: Date): string {
  const year = date.getFullYear()
  const month = pad(date.getMonth() + 1)
  const day = pad(date.getDate())
  const hour = pad(date.getHours())
  const minute = pad(date.getMinutes())
  const second = pad(date.getSeconds())
  return `${year}${month}${day}-${hour}${minute}${second}`
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}
