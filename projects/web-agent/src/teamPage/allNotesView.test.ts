// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupChat, WebAgentStore, RichNoteDocument } from '../group/types'
import type { NoteEditorAdapter, NoteEditorFactory } from './notesView'
import { collectNoteItems, createAllNotesView } from './allNotesView'

afterEach(() => {
  vi.useRealTimers()
  document.body.replaceChildren()
})

describe('all notes view', () => {
  it('collects global, live chats, and deleted chat notes for the notebook sidebar', () => {
    const liveChat = makeChat('chat-live', '产品群')
    const emptyChat = makeChat('chat-empty', '空笔记群')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      chatOrder: [liveChat.id, emptyChat.id],
      chatsById: { [liveChat.id]: liveChat, [emptyChat.id]: emptyChat },
      globalNote: note('全局记录'),
      chatNotesById: {
        [liveChat.id]: note('群聊记录'),
        'chat-deleted-123456': note('删除后还在的记录'),
      },
    }

    const items = collectNoteItems(store)

    expect(items.map(item => item.title)).toEqual(['全局笔记', '产品群', '空笔记群', '已删除群聊 chat-del…'])
    expect(items.map(item => item.meta)).toEqual(['手动记录', '群聊笔记', '群聊笔记', '已删除群聊的笔记'])
  })

  it('opens from the rail note button and edits the current chat note by default', () => {
    const chat = makeChat('chat-1', '当前群')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      chatNotesById: {
        [chat.id]: note('当前群笔记'),
      },
    }
    const { view, editor } = setupAllNotesView(store, () => chat)

    view.registerAllNotesEvents()
    document.querySelector<HTMLButtonElement>('#open-all-notes')?.click()

    expect(document.querySelector<HTMLElement>('#all-notes-modal')?.hidden).toBe(false)
    expect(document.querySelector<HTMLElement>('[data-note-target-id="chat-1"]')?.classList.contains('active')).toBe(true)
    expect(editor.setContent).toHaveBeenLastCalledWith(note('当前群笔记'))
  })

  it('renders global note labels in English mode', () => {
    const store = createDefaultStore()
    const { view } = setupAllNotesView(store, undefined, undefined, 'en')

    view.registerAllNotesEvents()
    document.querySelector<HTMLButtonElement>('#open-all-notes')?.click()

    expect(document.querySelector<HTMLElement>('#all-notes-active-title')?.textContent).toBe('Global notes')
    expect(document.querySelector<HTMLElement>('#all-notes-active-meta')?.textContent).toBe('Manual note')
    expect(document.querySelector<HTMLElement>('#all-notes-list')?.textContent).not.toContain('全局笔记')
  })

  it('switches note targets and saves rich text edits to the selected chat note', () => {
    vi.useFakeTimers()
    const chat = makeChat('chat-1', '群聊')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      globalNote: note('全局记录'),
      chatNotesById: { [chat.id]: note('群聊记录') },
    }
    const runCommand = vi.fn(async () => undefined)
    const { view, editor, updateEditor } = setupAllNotesView(store, () => chat, runCommand)

    view.registerAllNotesEvents()
    document.querySelector<HTMLButtonElement>('#open-all-notes')?.click()
    document.querySelector<HTMLButtonElement>('[data-note-target-id="global"]')?.click()
    expect(editor.setContent).toHaveBeenLastCalledWith(note('全局记录'))

    document.querySelector<HTMLButtonElement>('[data-note-target-id="chat-1"]')?.click()
    updateEditor()
    vi.advanceTimersByTime(250)

    expect(runCommand).toHaveBeenCalledWith('GROUP_NOTE_SAVE', {
      scope: 'chat',
      chatId: chat.id,
      content: note('已编辑'),
    })
  })

  it('keeps deleted chat notes editable from the notebook sidebar', () => {
    const store: WebAgentStore = {
      ...createDefaultStore(),
      chatNotesById: {
        'deleted-chat': note('这条笔记不能随着群聊消失'),
      },
    }
    const { view, editor } = setupAllNotesView(store)

    view.registerAllNotesEvents()
    document.querySelector<HTMLButtonElement>('#open-all-notes')?.click()
    document.querySelector<HTMLButtonElement>('[data-note-target-id="deleted-chat"]')?.click()

    expect(document.querySelector('.all-note-target.deleted-chat')).not.toBeNull()
    expect(editor.setContent).toHaveBeenLastCalledWith(note('这条笔记不能随着群聊消失'))
  })
})

function setupAllNotesView(
  store: WebAgentStore,
  getCurrentChat: () => GroupChat | undefined = () => undefined,
  runCommand = vi.fn(async () => undefined),
  language: WebAgentStore['settings']['language'] = 'zh-CN',
): { view: ReturnType<typeof createAllNotesView>; editor: NoteEditorAdapter; updateEditor: () => void } {
  store.settings.language = language
  document.body.innerHTML = `
    <button id="open-all-notes"></button>
    <div id="all-notes-modal" hidden>
      <button id="close-all-notes"></button>
      <div id="all-notes-list"></div>
      <h3 id="all-notes-active-title"></h3>
      <div id="all-notes-active-meta"></div>
      <div id="all-notes-editor"></div>
      <button id="all-note-bold"></button>
      <button id="all-note-italic"></button>
      <button id="all-note-strike"></button>
      <button id="all-note-bullet-list"></button>
      <button id="all-note-ordered-list"></button>
      <button id="all-note-undo"></button>
      <button id="all-note-redo"></button>
    </div>
  `
  let onUpdate = () => {}
  const editor = makeEditor()
  const createEditor: NoteEditorFactory = vi.fn(options => {
    onUpdate = options.onUpdate
    return editor
  })
  const view = createAllNotesView({
    openAllNotesEl: document.querySelector<HTMLButtonElement>('#open-all-notes')!,
    closeAllNotesEl: document.querySelector<HTMLButtonElement>('#close-all-notes')!,
    allNotesModalEl: document.querySelector<HTMLElement>('#all-notes-modal')!,
    allNotesListEl: document.querySelector<HTMLElement>('#all-notes-list')!,
    allNotesActiveTitleEl: document.querySelector<HTMLElement>('#all-notes-active-title')!,
    allNotesActiveMetaEl: document.querySelector<HTMLElement>('#all-notes-active-meta')!,
    allNotesEditorEl: document.querySelector<HTMLElement>('#all-notes-editor')!,
    noteToolbarButtons: {
      bold: document.querySelector<HTMLButtonElement>('#all-note-bold')!,
      italic: document.querySelector<HTMLButtonElement>('#all-note-italic')!,
      strike: document.querySelector<HTMLButtonElement>('#all-note-strike')!,
      bulletList: document.querySelector<HTMLButtonElement>('#all-note-bullet-list')!,
      orderedList: document.querySelector<HTMLButtonElement>('#all-note-ordered-list')!,
      undo: document.querySelector<HTMLButtonElement>('#all-note-undo')!,
      redo: document.querySelector<HTMLButtonElement>('#all-note-redo')!,
    },
    createEditor,
    getStore: () => store,
    getCurrentChat,
    runCommand,
    showError: vi.fn(),
  })
  return { view, editor, updateEditor: () => onUpdate() }
}

function makeEditor(): NoteEditorAdapter {
  return {
    setContent: vi.fn(),
    getJSON: vi.fn(() => note('已编辑')),
    insertText: vi.fn(),
    focus: vi.fn(),
    destroy: vi.fn(),
    runCommand: vi.fn(),
  }
}

function note(text: string): RichNoteDocument {
  return { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] }
}

function makeChat(id: string, name: string): GroupChat {
  return {
    id,
    name,
    mode: 'independent',
    roleIds: [],
    messageIds: [],
    nextMessageSeq: 1,
    status: 'ready',
    createdAt: 1,
    updatedAt: 1,
  }
}
