import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('team page chat list view boundary', () => {
  it('keeps chat list rendering and menu actions outside the team page entrypoint', () => {
    const entrySource = readFileSync(resolve(process.cwd(), 'src/teamPage/index.ts'), 'utf8')
    const viewSource = readFileSync(resolve(process.cwd(), 'src/teamPage/chatListView.ts'), 'utf8')

    expect(viewSource).toContain('function renderChatList()')
    expect(viewSource).toContain('function chatActionMenu(chat: GroupChat)')
    expect(viewSource).toContain('function switchChat(chatId: string)')
    expect(viewSource).toContain('function clearChatMessages(chatId: string)')
    expect(viewSource).toContain('function closeChatFrames(chatId: string)')
    expect(viewSource).toContain('function exportChatRecord(chat: GroupChat): void')
    expect(viewSource).toContain('function deleteChat(chatId: string)')
    expect(viewSource).toContain("exportRecord.textContent = ui('导出记录')")
    expect(viewSource).toContain("runCommand('GROUP_CHAT_UPDATE'")
    expect(viewSource).toContain('formatChatExportMarkdown')
    expect(viewSource).toContain('safeChatExportFilename')
    expect(viewSource).toContain("sendRuntimeMessage('GROUP_CHAT_DELETE'")
    expect(viewSource).toContain('chat-delete:fallback-local-store')
    expect(entrySource).not.toContain('function renderChatList()')
    expect(entrySource).not.toContain('function chatActionMenu(chat: GroupChat)')
    expect(entrySource).not.toContain('function switchChat(chatId: string)')
    expect(entrySource).not.toContain('function clearChatMessages(chatId: string)')
    expect(entrySource).not.toContain('function closeChatFrames(chatId: string)')
    expect(entrySource).not.toContain('function exportChatRecord(chat: GroupChat): void')
    expect(entrySource).not.toContain('function deleteChat(chatId: string)')
  })
})
