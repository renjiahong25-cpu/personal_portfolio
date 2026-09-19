export function setContentEditableText(editor: HTMLElement, content: string): void {
  editor.focus()
  editor.replaceChildren()

  const block = document.createElement('p')
  block.textContent = content
  editor.append(block)

  editor.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, inputType: 'insertText', data: content }))
  editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: content }))
  editor.dispatchEvent(new Event('change', { bubbles: true }))
}

export function readEditorText(editor: HTMLElement, options: { normalizeNbsp?: boolean } = {}): string {
  const raw = editor.innerText || editor.textContent || ''
  const text = options.normalizeNbsp ? raw.replace(/\u00a0/g, ' ') : raw
  return text.trim()
}
