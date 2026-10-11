import type { WebAgentStore, RichNoteDocument } from '../group/types'
import { normalizeLanguage, translateUi } from '../shared/i18n'
import type { NoteEditorAdapter, NoteEditorFactory, NoteScope, NoteToolbarCommand } from './notesView'

export interface AllNotesViewDependencies {
  openAllNotesEl: HTMLButtonElement
  closeAllNotesEl: HTMLButtonElement
  allNotesModalEl: HTMLElement
  allNotesListEl: HTMLElement
  allNotesActiveTitleEl: HTMLElement
  allNotesActiveMetaEl: HTMLElement
  allNotesEditorEl: HTMLElement
  noteToolbarButtons: Record<NoteToolbarCommand, HTMLButtonElement>
  createEditor?: NoteEditorFactory
  getStore(): WebAgentStore
  getCurrentChat(): { id: string } | undefined
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
  showError(message: string): void
}

export interface AllNotesView {
  renderAllNotes(): void
  registerAllNotesEvents(): void
  destroy(): void
}

interface NoteListItem {
  id: string
  scope: NoteScope
  chatId?: string
  title: string
  meta: string
  content: RichNoteDocument
  deletedChat: boolean
}

const EMPTY_NOTE: RichNoteDocument = { type: 'doc', content: [{ type: 'paragraph' }] }

export function createAllNotesView(deps: AllNotesViewDependencies): AllNotesView {
  const createEditor = deps.createEditor ?? createTiptapNoteEditor
  let editor: NoteEditorAdapter | undefined
  let editorLoadPromise: Promise<NoteEditorAdapter> | undefined
  let activeTarget: NoteListItem | undefined
  let loadedTargetId: string | undefined
  let saveTimer: number | undefined
  let hasUnsavedChanges = false

  function renderAllNotes(): void {
    const items = collectNoteItems(deps.getStore())
    activeTarget = selectAvailableTarget(items)
    deps.allNotesListEl.replaceChildren()

    if (items.length === 0) {
      const empty = document.createElement('div')
      empty.className = 'all-notes-empty'
      empty.textContent = ui('还没有笔记')
      deps.allNotesListEl.append(empty)
      return
    }

    for (const item of items) deps.allNotesListEl.append(renderNoteTargetButton(item))
    renderActiveTarget(false)
  }

  function registerAllNotesEvents(): void {
    deps.openAllNotesEl.addEventListener('click', () => {
      deps.allNotesModalEl.hidden = false
      renderAllNotes()
      ensureEditor()
      focusEditorWhenReady()
    })
    deps.closeAllNotesEl.addEventListener('click', closeAllNotes)
    deps.allNotesModalEl.addEventListener('click', event => {
      if (event.target === deps.allNotesModalEl) closeAllNotes()
    })
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !deps.allNotesModalEl.hidden) closeAllNotes()
    })
    for (const [command, button] of Object.entries(deps.noteToolbarButtons) as Array<[NoteToolbarCommand, HTMLButtonElement]>) {
      button.addEventListener('click', () => editor?.runCommand(command))
    }
  }

  function closeAllNotes(): void {
    saveActiveNote()
    deps.allNotesModalEl.hidden = true
  }

  function destroy(): void {
    saveActiveNote()
    editor?.destroy()
  }

  function selectAvailableTarget(items: NoteListItem[]): NoteListItem | undefined {
    if (activeTarget && items.some(item => item.id === activeTarget?.id)) {
      return items.find(item => item.id === activeTarget?.id)
    }
    const currentChatId = deps.getCurrentChat()?.id
    return items.find(item => item.chatId === currentChatId) ?? items[0]
  }

  function renderNoteTargetButton(item: NoteListItem): HTMLButtonElement {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = `all-note-target${item.deletedChat ? ' deleted-chat' : ''}${activeTarget?.id === item.id ? ' active' : ''}`
    button.dataset.noteTargetId = item.id
    button.setAttribute('aria-pressed', String(activeTarget?.id === item.id))
    const title = document.createElement('span')
    title.className = 'all-note-target-title'
    title.textContent = ui(item.title)
    const meta = document.createElement('span')
    meta.className = 'all-note-target-meta'
    meta.textContent = ui(item.meta)
    button.append(title, meta)
    button.addEventListener('click', () => {
      saveActiveNote()
      activeTarget = item
      renderAllNotes()
      renderActiveTarget(false)
      ensureEditor()
      focusEditorWhenReady()
    })
    return button
  }

  function renderActiveTarget(forceContent = false): void {
    if (!activeTarget) return
    deps.allNotesActiveTitleEl.textContent = ui(activeTarget.title)
    deps.allNotesActiveMetaEl.textContent = ui(activeTarget.meta)
    if (forceContent || loadedTargetId !== activeTarget.id) {
      loadedTargetId = activeTarget.id
      editor?.setContent(activeTarget.content)
    }
  }

  function ensureEditor(): Promise<NoteEditorAdapter> | undefined {
    if (!activeTarget) return undefined
    if (editor) return Promise.resolve(editor)
    if (editorLoadPromise) return editorLoadPromise
    const created = createEditor({
      element: deps.allNotesEditorEl,
      content: activeTarget.content,
      onUpdate: scheduleSaveActiveNote,
    })
    if (!isPromise(created)) {
      editor = created
      renderActiveTarget(true)
      return Promise.resolve(editor)
    }
    editorLoadPromise = created
      .then(createdEditor => {
        editor = createdEditor
        editorLoadPromise = undefined
        renderActiveTarget(true)
        if (!deps.allNotesModalEl.hidden) editor.focus()
        return editor
      })
      .catch(error => {
        editorLoadPromise = undefined
        deps.showError(error instanceof Error ? error.message : String(error))
        throw error
      })
    return editorLoadPromise
  }

  function focusEditorWhenReady(): void {
    if (editor) {
      editor.focus()
      return
    }
    editorLoadPromise?.then(createdEditor => createdEditor.focus()).catch(() => undefined)
  }

  function ui(source: string): string {
    return translateUi(source, normalizeLanguage(deps.getStore().settings.language))
  }

  function scheduleSaveActiveNote(): void {
    hasUnsavedChanges = true
    if (saveTimer !== undefined) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(saveActiveNote, 250)
  }

  function saveActiveNote(): void {
    if (!editor || !activeTarget) return
    if (saveTimer !== undefined) {
      window.clearTimeout(saveTimer)
      saveTimer = undefined
    }
    if (!hasUnsavedChanges) return
    hasUnsavedChanges = false
    deps.runCommand('GROUP_NOTE_SAVE', {
      scope: activeTarget.scope,
      ...(activeTarget.chatId ? { chatId: activeTarget.chatId } : {}),
      content: editor.getJSON(),
    }).catch(error => deps.showError(error instanceof Error ? error.message : String(error)))
  }

  return { renderAllNotes, registerAllNotesEvents, destroy }
}

export function collectNoteItems(store: WebAgentStore): NoteListItem[] {
  const items: NoteListItem[] = []
  items.push({
    id: 'global',
    scope: 'global',
    title: '全局笔记',
    meta: '手动记录',
    content: store.globalNote ?? EMPTY_NOTE,
    deletedChat: false,
  })

  const chatNotes = store.chatNotesById ?? {}
  const orderedChatIds = [
    ...store.chatOrder,
    ...Object.keys(chatNotes).filter(chatId => !store.chatOrder.includes(chatId)).sort(),
  ]
  for (const chatId of orderedChatIds) {
    const chat = store.chatsById[chatId]
    const content = chatNotes[chatId] ?? EMPTY_NOTE
    if (!chat && isEmptyNote(content)) continue
    items.push({
      id: chatId,
      scope: 'chat',
      chatId,
      title: chat?.name ?? `已删除群聊 ${shortId(chatId)}`,
      meta: chat ? '群聊笔记' : '已删除群聊的笔记',
      content,
      deletedChat: !chat,
    })
  }

  return items
}

function isEmptyNote(document: RichNoteDocument): boolean {
  return notePlainText(document).trim().length === 0
}

function notePlainText(node: RichNoteDocument): string {
  const ownText = typeof node.text === 'string' ? node.text : ''
  const childText = Array.isArray(node.content) ? node.content.map(notePlainText).join(' ') : ''
  return `${ownText} ${childText}`.trim()
}

function shortId(chatId: string): string {
  return chatId.length > 8 ? `${chatId.slice(0, 8)}…` : chatId
}

function isPromise(value: NoteEditorAdapter | Promise<NoteEditorAdapter>): value is Promise<NoteEditorAdapter> {
  return typeof (value as Promise<NoteEditorAdapter>).then === 'function'
}

async function createTiptapNoteEditor(options: { element: HTMLElement; content: RichNoteDocument; onUpdate(): void }): Promise<NoteEditorAdapter> {
  const module = await import('./tiptapNoteEditor')
  return module.createTiptapNoteEditor(options)
}
