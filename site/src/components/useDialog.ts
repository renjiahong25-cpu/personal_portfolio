import { onBeforeUnmount, onMounted, type Ref } from 'vue'
export function useDialog(root: Ref<HTMLElement | null>, close: () => void): void {
  let previous: HTMLElement | null = null
  const keyboard = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close() }
    if (e.key !== 'Tab' || !root.value) return
    const focusable = Array.from(root.value.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,select,video,[tabindex="0"]')).filter(el => el.getClientRects().length > 0)
    const first = focusable[0], last = focusable.at(-1)
    if (!first || !last) { e.preventDefault(); root.value.focus(); return }
    if (e.shiftKey && (document.activeElement === first || !root.value.contains(document.activeElement))) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && (document.activeElement === last || !root.value.contains(document.activeElement))) { e.preventDefault(); first.focus() }
  }
  onMounted(() => { previous = document.activeElement instanceof HTMLElement ? document.activeElement : null; root.value?.focus(); window.addEventListener('keydown', keyboard, true) })
  onBeforeUnmount(() => { window.removeEventListener('keydown', keyboard, true); previous?.focus() })
}
