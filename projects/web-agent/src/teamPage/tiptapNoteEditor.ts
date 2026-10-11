import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import type { RichNoteDocument } from '../group/types'
import type { NoteEditorAdapter } from './notesView'

export function createTiptapNoteEditor(options: { element: HTMLElement; content: RichNoteDocument; onUpdate(): void }): NoteEditorAdapter {
  const editor = new Editor({
    element: options.element,
    extensions: [StarterKit],
    content: options.content,
    onUpdate: options.onUpdate,
  })

  return {
    setContent(content) {
      editor.commands.setContent(content, { emitUpdate: false })
    },
    getJSON() {
      return editor.getJSON()
    },
    insertText(text) {
      editor.chain().focus().insertContent(text).run()
    },
    focus() {
      editor.commands.focus()
    },
    destroy() {
      editor.destroy()
    },
    runCommand(command) {
      const chain = editor.chain().focus()
      if (command === 'bold') chain.toggleBold().run()
      if (command === 'italic') chain.toggleItalic().run()
      if (command === 'strike') chain.toggleStrike().run()
      if (command === 'bulletList') chain.toggleBulletList().run()
      if (command === 'orderedList') chain.toggleOrderedList().run()
      if (command === 'undo') chain.undo().run()
      if (command === 'redo') chain.redo().run()
    },
  }
}
