import { createLogger } from '../shared/logger'

export const teamPageLog = createLogger('team-page')

export function createErrorPresenter(errorEl: HTMLElement): (message: string) => void {
  return message => {
    errorEl.classList.remove('toast-success')
    errorEl.textContent = message
    errorEl.hidden = false
    window.setTimeout(() => {
      errorEl.hidden = true
    }, 5200)
  }
}

export function createSuccessPresenter(errorEl: HTMLElement): (message: string) => void {
  return message => {
    errorEl.classList.add('toast-success')
    errorEl.textContent = message
    errorEl.hidden = false
    window.setTimeout(() => {
      errorEl.hidden = true
      errorEl.classList.remove('toast-success')
    }, 2200)
  }
}
