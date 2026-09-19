import type { GroupChat, WebAgentStore, RichNoteDocument } from '../group/types'
import type { TeamPageState } from './appState'

export type NoteScope = 'global' | 'chat'

export interface NoteEditorAdapter {
  setContent(content: RichNoteDocument): void
  getJSON(): RichNoteDocument
  insertText(text: string): void
  focus(): void
  destroy(): void
  runCommand(command: NoteToolbarCommand): void
}

export type NoteToolbarCommand = 'bold' | 'italic' | 'strike' | 'bulletList' | 'orderedList' | 'undo' | 'redo'

export type NoteEditorFactory = (options: { element: HTMLElement; content: RichNoteDocument; onUpdate(): void }) => NoteEditorAdapter | Promise<NoteEditorAdapter>

export interface NotesViewDependencies {
  state: TeamPageState
  notesPanelEl: HTMLElement
  notesDragHandleEl: HTMLElement
  notesResizeHandleEl: HTMLElement
  toggleNotesPanelEl: HTMLButtonElement
  closeNotesPanelEl: HTMLButtonElement
  globalNoteTabEl: HTMLButtonElement
  chatNoteTabEl: HTMLButtonElement
  notesEditorEl: HTMLElement
  noteToolbarButtons: Record<NoteToolbarCommand, HTMLButtonElement>
  createEditor?: NoteEditorFactory
  getStore(): WebAgentStore
  getCurrentChat(): GroupChat | undefined
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
  showError(message: string): void
}

export interface NotesView {
  renderNotes(): void
  registerNotesEvents(): void
  insertTextIntoActiveNote(text: string): void
  destroy(): void
}

const EMPTY_NOTE: RichNoteDocument = { type: 'doc', content: [{ type: 'paragraph' }] }
const FLOATING_PANEL_MARGIN = 12
const MIN_NOTES_PANEL_WIDTH = 320
const MIN_NOTES_PANEL_HEIGHT = 360

export function createNotesView(deps: NotesViewDependencies): NotesView {
  const createEditor = deps.createEditor ?? createTiptapNoteEditor
  let editor: NoteEditorAdapter | undefined
  let editorLoadPromise: Promise<NoteEditorAdapter> | undefined
  let loadedScope: NoteScope | undefined
  let loadedChatId: string | undefined
  let saveTimer: number | undefined
  let dragStart: { pointerId: number; startX: number; startY: number; startLeft: number; startTop: number } | undefined
  let resizeStart: { pointerId: number; startX: number; startY: number; startWidth: number; startHeight: number; startLeft: number; startTop: number } | undefined

  function renderNotes(): void {
    const chat = deps.getCurrentChat()
    const scope = readAvailableScope(chat)

    deps.notesPanelEl.classList.toggle('open', deps.state.notesPanelOpen)
    deps.toggleNotesPanelEl.setAttribute('aria-expanded', String(deps.state.notesPanelOpen))
    if (deps.state.notesPanelOpen) clampFloatingPanelPosition()
    deps.globalNoteTabEl.classList.toggle('active', scope === 'global')
    deps.chatNoteTabEl.classList.toggle('active', scope === 'chat')
    deps.chatNoteTabEl.disabled = !chat

    if (deps.state.notesPanelOpen) ensureEditor()?.catch(() => undefined)
    const nextChatId = scope === 'chat' ? chat?.id : undefined
    if (loadedScope !== scope || loadedChatId !== nextChatId) {
      loadedScope = scope
      loadedChatId = nextChatId
      editor?.setContent(readNoteContent(scope, nextChatId))
    }
  }

  function registerNotesEvents(): void {
    deps.toggleNotesPanelEl.addEventListener('click', () => {
      const nextOpen = !deps.state.notesPanelOpen
      if (nextOpen) selectDefaultOpenScope()
      deps.state.notesPanelOpen = nextOpen
      renderNotes()
      if (deps.state.notesPanelOpen) {
        ensureEditor()
        focusEditorWhenReady()
      }
    })
    deps.closeNotesPanelEl.addEventListener('click', () => {
      deps.state.notesPanelOpen = false
      renderNotes()
    })
    deps.globalNoteTabEl.addEventListener('click', () => {
      deps.state.activeNoteScope = 'global'
      renderNotes()
    })
    deps.chatNoteTabEl.addEventListener('click', () => {
      if (!deps.getCurrentChat()) return
      deps.state.activeNoteScope = 'chat'
      renderNotes()
    })
    for (const [command, button] of Object.entries(deps.noteToolbarButtons) as Array<[NoteToolbarCommand, HTMLButtonElement]>) {
      button.addEventListener('click', () => editor?.runCommand(command))
    }
    deps.notesDragHandleEl.addEventListener('pointerdown', startDragging)
    deps.notesResizeHandleEl.addEventListener('pointerdown', startResizing)
    window.addEventListener('pointermove', moveDraggingPanel)
    window.addEventListener('pointermove', resizeFloatingPanel)
    window.addEventListener('pointerup', stopDragging)
    window.addEventListener('pointerup', stopResizing)
    window.addEventListener('pointercancel', stopDragging)
    window.addEventListener('pointercancel', stopResizing)
    window.addEventListener('resize', clampFloatingPanelPosition)
  }

  function insertTextIntoActiveNote(text: string): void {
    const trimmed = text.trim()
    if (!trimmed) return
    selectDefaultOpenScope()
    deps.state.notesPanelOpen = true
    renderNotes()
    if (editor) {
      editor?.insertText(trimmed)
      saveActiveNote()
      return
    }
    ensureEditor()?.then(createdEditor => {
      createdEditor.insertText(trimmed)
      saveActiveNote()
    }).catch(() => undefined)
  }

  function destroy(): void {
    if (saveTimer !== undefined) window.clearTimeout(saveTimer)
    deps.notesDragHandleEl.removeEventListener('pointerdown', startDragging)
    deps.notesResizeHandleEl.removeEventListener('pointerdown', startResizing)
    window.removeEventListener('pointermove', moveDraggingPanel)
    window.removeEventListener('pointermove', resizeFloatingPanel)
    window.removeEventListener('pointerup', stopDragging)
    window.removeEventListener('pointerup', stopResizing)
    window.removeEventListener('pointercancel', stopDragging)
    window.removeEventListener('pointercancel', stopResizing)
    window.removeEventListener('resize', clampFloatingPanelPosition)
    editor?.destroy()
  }

  function startDragging(event: PointerEvent): void {
    if (event.button !== 0) return
    const target = event.target as Element | null
    if (target?.closest('button, input, textarea, select, a, [role="button"]')) return

    const rect = deps.notesPanelEl.getBoundingClientRect()
    deps.notesPanelEl.style.left = `${rect.left}px`
    deps.notesPanelEl.style.top = `${rect.top}px`
    deps.notesPanelEl.style.right = 'auto'
    deps.notesPanelEl.style.bottom = 'auto'
    deps.notesPanelEl.classList.add('dragging')
    dragStart = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startLeft: rect.left,
      startTop: rect.top,
    }
    deps.notesDragHandleEl.setPointerCapture?.(event.pointerId)
    event.preventDefault()
    event.stopPropagation()
  }

  function moveDraggingPanel(event: PointerEvent): void {
    if (!dragStart || dragStart.pointerId !== event.pointerId) return
    const nextLeft = dragStart.startLeft + event.clientX - dragStart.startX
    const nextTop = dragStart.startTop + event.clientY - dragStart.startY
    moveFloatingPanelTo(nextLeft, nextTop)
  }

  function stopDragging(event: PointerEvent): void {
    if (!dragStart || dragStart.pointerId !== event.pointerId) return
    dragStart = undefined
    deps.notesPanelEl.classList.remove('dragging')
    if (deps.notesDragHandleEl.hasPointerCapture?.(event.pointerId)) deps.notesDragHandleEl.releasePointerCapture(event.pointerId)
  }

  function startResizing(event: PointerEvent): void {
    if (event.button !== 0) return

    const rect = deps.notesPanelEl.getBoundingClientRect()
    deps.notesPanelEl.style.left = `${rect.left}px`
    deps.notesPanelEl.style.top = `${rect.top}px`
    deps.notesPanelEl.style.right = 'auto'
    deps.notesPanelEl.style.bottom = 'auto'
    deps.notesPanelEl.classList.add('resizing')
    resizeStart = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startWidth: rect.width,
      startHeight: rect.height,
      startLeft: rect.left,
      startTop: rect.top,
    }
    deps.notesResizeHandleEl.setPointerCapture?.(event.pointerId)
    event.preventDefault()
    event.stopPropagation()
  }

  function resizeFloatingPanel(event: PointerEvent): void {
    if (!resizeStart || resizeStart.pointerId !== event.pointerId) return
    const nextWidth = resizeStart.startWidth + event.clientX - resizeStart.startX
    const nextHeight = resizeStart.startHeight + event.clientY - resizeStart.startY
    resizeFloatingPanelTo(nextWidth, nextHeight, resizeStart.startLeft, resizeStart.startTop)
  }

  function stopResizing(event: PointerEvent): void {
    if (!resizeStart || resizeStart.pointerId !== event.pointerId) return
    resizeStart = undefined
    deps.notesPanelEl.classList.remove('resizing')
    if (deps.notesResizeHandleEl.hasPointerCapture?.(event.pointerId)) deps.notesResizeHandleEl.releasePointerCapture(event.pointerId)
  }

  function clampFloatingPanelPosition(): void {
    if (!deps.notesPanelEl.classList.contains('open')) return
    if (!deps.notesPanelEl.style.left || !deps.notesPanelEl.style.top) return
    const rect = deps.notesPanelEl.getBoundingClientRect()
    resizeFloatingPanelTo(rect.width, rect.height, rect.left, rect.top)
    moveFloatingPanelTo(rect.left, rect.top)
  }

  function moveFloatingPanelTo(left: number, top: number): void {
    const rect = deps.notesPanelEl.getBoundingClientRect()
    const panelWidth = Math.min(rect.width, window.innerWidth - FLOATING_PANEL_MARGIN * 2)
    const panelHeight = Math.min(rect.height, window.innerHeight - FLOATING_PANEL_MARGIN * 2)
    const maxLeft = Math.max(FLOATING_PANEL_MARGIN, window.innerWidth - panelWidth - FLOATING_PANEL_MARGIN)
    const maxTop = Math.max(FLOATING_PANEL_MARGIN, window.innerHeight - panelHeight - FLOATING_PANEL_MARGIN)
    deps.notesPanelEl.style.left = `${Math.min(Math.max(FLOATING_PANEL_MARGIN, left), maxLeft)}px`
    deps.notesPanelEl.style.top = `${Math.min(Math.max(FLOATING_PANEL_MARGIN, top), maxTop)}px`
    deps.notesPanelEl.style.right = 'auto'
    deps.notesPanelEl.style.bottom = 'auto'
  }

  function resizeFloatingPanelTo(width: number, height: number, left: number, top: number): void {
    const maxWidth = Math.max(MIN_NOTES_PANEL_WIDTH, window.innerWidth - left - FLOATING_PANEL_MARGIN)
    const maxHeight = Math.max(MIN_NOTES_PANEL_HEIGHT, window.innerHeight - top - FLOATING_PANEL_MARGIN)
    deps.notesPanelEl.style.width = `${Math.min(Math.max(MIN_NOTES_PANEL_WIDTH, width), maxWidth)}px`
    deps.notesPanelEl.style.height = `${Math.min(Math.max(MIN_NOTES_PANEL_HEIGHT, height), maxHeight)}px`
    deps.notesPanelEl.style.right = 'auto'
    deps.notesPanelEl.style.bottom = 'auto'
  }

  function ensureEditor(): Promise<NoteEditorAdapter> | undefined {
    if (editor) return Promise.resolve(editor)
    if (editorLoadPromise) return editorLoadPromise

    const currentScope = readAvailableScope(deps.getCurrentChat())
    const currentChatId = currentScope === 'chat' ? deps.getCurrentChat()?.id : undefined
    const created = createEditor({
      element: deps.notesEditorEl,
      content: readNoteContent(currentScope, currentChatId),
      onUpdate: scheduleSaveActiveNote,
    })
    if (!isPromise(created)) {
      editor = created
      syncLoadedContent()
      return Promise.resolve(editor)
    }

    editorLoadPromise = created
      .then(createdEditor => {
        editor = createdEditor
        editorLoadPromise = undefined
        syncLoadedContent()
        if (deps.state.notesPanelOpen) editor.focus()
        return editor
      })
      .catch(error => {
        editorLoadPromise = undefined
        handleEditorLoadError(error)
        throw error
      })
    return editorLoadPromise
  }

  function syncLoadedContent(): void {
    if (!editor || !loadedScope) return
    editor.setContent(readNoteContent(loadedScope, loadedChatId))
  }

  function focusEditorWhenReady(): void {
    if (editor) {
      editor.focus()
      return
    }
    editorLoadPromise?.then(createdEditor => createdEditor.focus()).catch(() => undefined)
  }

  function handleEditorLoadError(error: unknown): void {
    deps.showError(error instanceof Error ? error.message : String(error))
  }

  function readAvailableScope(chat: GroupChat | undefined): NoteScope {
    return deps.state.activeNoteScope === 'chat' && chat ? 'chat' : 'global'
  }

  function selectDefaultOpenScope(): void {
    if (deps.getCurrentChat()) deps.state.activeNoteScope = 'chat'
  }

  function readNoteContent(scope: NoteScope, chatId: string | undefined): RichNoteDocument {
    const store = deps.getStore()
    if (scope === 'chat' && chatId) return store.chatNotesById?.[chatId] ?? EMPTY_NOTE
    return store.globalNote ?? EMPTY_NOTE
  }

  function scheduleSaveActiveNote(): void {
    if (saveTimer !== undefined) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(saveActiveNote, 250)
  }

  function saveActiveNote(): void {
    if (!editor) return
    if (saveTimer !== undefined) {
      window.clearTimeout(saveTimer)
      saveTimer = undefined
    }
    const scope = loadedScope ?? readAvailableScope(deps.getCurrentChat())
    const chatId = scope === 'chat' ? loadedChatId ?? deps.getCurrentChat()?.id : undefined
    deps.runCommand('GROUP_NOTE_SAVE', {
      scope,
      ...(chatId ? { chatId } : {}),
      content: editor.getJSON(),
    }).catch(error => deps.showError(error instanceof Error ? error.message : String(error)))
  }

  return { renderNotes, registerNotesEvents, insertTextIntoActiveNote, destroy }
}

function isPromise(value: NoteEditorAdapter | Promise<NoteEditorAdapter>): value is Promise<NoteEditorAdapter> {
  return typeof (value as Promise<NoteEditorAdapter>).then === 'function'
}

async function createTiptapNoteEditor(options: { element: HTMLElement; content: RichNoteDocument; onUpdate(): void }): Promise<NoteEditorAdapter> {
  const module = await import('./tiptapNoteEditor')
  return module.createTiptapNoteEditor(options)
}
