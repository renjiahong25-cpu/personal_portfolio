// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import type { GroupChat, WebAgentStore, RichNoteDocument } from '../group/types'
import { createTeamPageState } from './appState'
import { createNotesView, type NoteEditorAdapter, type NoteEditorFactory } from './notesView'

describe('team page notes view', () => {
  it('does not load the rich text editor until notes are opened', () => {
    const chat = makeChat('chat-1')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
    }
    const createEditor = vi.fn(() => makeEditor())
    const { view } = setupNotesView(store, chat, undefined, createEditor)

    view.renderNotes()

    expect(createEditor).not.toHaveBeenCalled()
  })

  it('loads global and chat notes into one rich text editor when the scope changes', () => {
    const chat = makeChat('chat-1')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      globalNote: note('全局笔记'),
      chatNotesById: { [chat.id]: note('群聊笔记') },
    }
    const { view, editor } = setupNotesView(store, chat)

    view.registerNotesEvents()
    document.querySelector<HTMLButtonElement>('#toggle-notes-panel')?.click()
    expect(editor.setContent).toHaveBeenLastCalledWith(note('群聊笔记'))

    document.querySelector<HTMLButtonElement>('[data-note-scope="global"]')?.click()

    expect(editor.setContent).toHaveBeenLastCalledWith(note('全局笔记'))
  })

  it('defaults back to the current chat note when notes are opened after rendering without a chat', () => {
    const chat = makeChat('chat-1')
    let currentChat: GroupChat | undefined
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
      globalNote: note('全局笔记'),
      chatNotesById: { [chat.id]: note('群聊笔记') },
    }
    const { view, editor } = setupNotesView(store, () => currentChat)

    view.registerNotesEvents()
    view.renderNotes()
    currentChat = chat
    document.querySelector<HTMLButtonElement>('#toggle-notes-panel')?.click()

    expect(editor.setContent).toHaveBeenLastCalledWith(note('群聊笔记'))
  })

  it('inserts selected message text into the active chat note without source metadata', () => {
    const chat = makeChat('chat-1')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
    }
    const runCommand = vi.fn(async () => undefined)
    const { view, editor } = setupNotesView(store, chat, runCommand)

    view.renderNotes()
    view.insertTextIntoActiveNote('重点内容')

    expect(editor.insertText).toHaveBeenCalledWith('重点内容')
    expect(editor.insertText).not.toHaveBeenCalledWith(expect.stringContaining('来源'))
    expect(runCommand).toHaveBeenCalledWith('GROUP_NOTE_SAVE', {
      scope: 'chat',
      chatId: chat.id,
      content: editor.getJSON(),
    })
  })

  it('lets the note window be dragged without leaving the viewport', () => {
    const chat = makeChat('chat-1')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
    }
    const { view } = setupNotesView(store, chat)
    const panel = document.querySelector<HTMLElement>('#notes-panel')!
    const handle = document.querySelector<HTMLElement>('#notes-drag-handle')!

    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({
      x: 420,
      y: 80,
      left: 420,
      top: 80,
      right: 860,
      bottom: 680,
      width: 440,
      height: 600,
      toJSON: () => ({}),
    } as DOMRect)
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 900 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 760 })

    view.registerNotesEvents()
    handle.dispatchEvent(pointerEvent('pointerdown', { clientX: 440, clientY: 100, pointerId: 1 }))
    window.dispatchEvent(pointerEvent('pointermove', { clientX: 540, clientY: 170, pointerId: 1 }))
    window.dispatchEvent(pointerEvent('pointerup', { clientX: 540, clientY: 170, pointerId: 1 }))

    expect(panel.style.left).toBe('448px')
    expect(panel.style.top).toBe('148px')
    expect(panel.style.right).toBe('auto')
    expect(panel.classList.contains('dragging')).toBe(false)
  })

  it('lets the note window be resized without exceeding the viewport', () => {
    const chat = makeChat('chat-1')
    const store: WebAgentStore = {
      ...createDefaultStore(),
      currentChatId: chat.id,
      chatOrder: [chat.id],
      chatsById: { [chat.id]: chat },
    }
    const { view } = setupNotesView(store, chat)
    const panel = document.querySelector<HTMLElement>('#notes-panel')!
    const handle = document.querySelector<HTMLElement>('#notes-resize-handle')!

    vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({
      x: 420,
      y: 80,
      left: 420,
      top: 80,
      right: 860,
      bottom: 680,
      width: 440,
      height: 600,
      toJSON: () => ({}),
    } as DOMRect)
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 900 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 760 })

    view.registerNotesEvents()
    handle.dispatchEvent(pointerEvent('pointerdown', { clientX: 860, clientY: 680, pointerId: 2 }))
    window.dispatchEvent(pointerEvent('pointermove', { clientX: 1060, clientY: 900, pointerId: 2 }))
    window.dispatchEvent(pointerEvent('pointerup', { clientX: 1060, clientY: 900, pointerId: 2 }))

    expect(panel.style.width).toBe('468px')
    expect(panel.style.height).toBe('668px')
    expect(panel.classList.contains('resizing')).toBe(false)
  })
})

function setupNotesView(
  store: WebAgentStore,
  chat: GroupChat | undefined | (() => GroupChat | undefined),
  runCommand = vi.fn(async () => undefined),
  createEditorOverride?: NoteEditorFactory,
): { view: ReturnType<typeof createNotesView>; editor: NoteEditorAdapter } {
  document.body.innerHTML = `
    <button id="toggle-notes-panel"></button>
    <aside id="notes-panel">
      <div id="notes-drag-handle"></div>
      <button id="notes-resize-handle"></button>
      <button id="close-notes-panel"></button>
      <button id="global-note-tab" data-note-scope="global"></button>
      <button id="chat-note-tab" data-note-scope="chat"></button>
      <div id="notes-editor"></div>
      <button id="note-bold"></button>
      <button id="note-italic"></button>
      <button id="note-strike"></button>
      <button id="note-bullet-list"></button>
      <button id="note-ordered-list"></button>
      <button id="note-undo"></button>
      <button id="note-redo"></button>
    </aside>
  `
  const editor = makeEditor()
  const createEditor: NoteEditorFactory = createEditorOverride ?? vi.fn(() => editor)
  const view = createNotesView({
    state: createTeamPageState(),
    notesPanelEl: document.querySelector<HTMLElement>('#notes-panel')!,
    notesDragHandleEl: document.querySelector<HTMLElement>('#notes-drag-handle')!,
    notesResizeHandleEl: document.querySelector<HTMLElement>('#notes-resize-handle')!,
    toggleNotesPanelEl: document.querySelector<HTMLButtonElement>('#toggle-notes-panel')!,
    closeNotesPanelEl: document.querySelector<HTMLButtonElement>('#close-notes-panel')!,
    globalNoteTabEl: document.querySelector<HTMLButtonElement>('#global-note-tab')!,
    chatNoteTabEl: document.querySelector<HTMLButtonElement>('#chat-note-tab')!,
    notesEditorEl: document.querySelector<HTMLElement>('#notes-editor')!,
    noteToolbarButtons: {
      bold: document.querySelector<HTMLButtonElement>('#note-bold')!,
      italic: document.querySelector<HTMLButtonElement>('#note-italic')!,
      strike: document.querySelector<HTMLButtonElement>('#note-strike')!,
      bulletList: document.querySelector<HTMLButtonElement>('#note-bullet-list')!,
      orderedList: document.querySelector<HTMLButtonElement>('#note-ordered-list')!,
      undo: document.querySelector<HTMLButtonElement>('#note-undo')!,
      redo: document.querySelector<HTMLButtonElement>('#note-redo')!,
    },
    createEditor,
    getStore: () => store,
    getCurrentChat: typeof chat === 'function' ? chat : () => chat,
    runCommand,
    showError: vi.fn(),
  })
  return { view, editor }
}

function makeEditor(): NoteEditorAdapter {
  return {
    setContent: vi.fn(),
    getJSON: vi.fn(() => note('已更新')),
    insertText: vi.fn(),
    focus: vi.fn(),
    destroy: vi.fn(),
    runCommand: vi.fn(),
  }
}

function pointerEvent(type: string, options: { clientX: number; clientY: number; pointerId: number }): PointerEvent {
  const event = new MouseEvent(type, { bubbles: true, clientX: options.clientX, clientY: options.clientY, button: 0 }) as PointerEvent
  Object.defineProperty(event, 'pointerId', { value: options.pointerId })
  return event
}

function note(text: string): RichNoteDocument {
  return { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] }
}

function makeChat(id: string): GroupChat {
  return {
    id,
    name: id,
    mode: 'independent',
    roleIds: [],
    messageIds: [],
    nextMessageSeq: 1,
    status: 'ready',
    createdAt: 1,
    updatedAt: 1,
  }
}
