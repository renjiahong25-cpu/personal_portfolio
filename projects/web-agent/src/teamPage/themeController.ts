export type TeamTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'web-agent.theme'

export interface ThemeControllerDependencies {
  root: HTMLElement
  lightButton: HTMLButtonElement
  darkButton: HTMLButtonElement
  storage?: Storage
}

export interface ThemeController {
  getTheme(): TeamTheme
  initializeTheme(): void
  registerThemeEvents(): void
  setTheme(theme: TeamTheme): void
}

export function createThemeController(deps: ThemeControllerDependencies): ThemeController {
  const storage = deps.storage ?? safeLocalStorage()
  let currentTheme: TeamTheme = normalizeTheme(deps.root.dataset.theme) ?? 'dark'
  let registered = false

  function setTheme(theme: TeamTheme): void {
    applyTheme(theme, true)
  }

  function initializeTheme(): void {
    applyTheme(readStoredTheme(storage) ?? normalizeTheme(deps.root.dataset.theme) ?? 'dark', false)
  }

  function registerThemeEvents(): void {
    if (registered) return
    registered = true
    deps.lightButton.addEventListener('click', () => setTheme('light'))
    deps.darkButton.addEventListener('click', () => setTheme('dark'))
  }

  function getTheme(): TeamTheme {
    return currentTheme
  }

  function applyTheme(theme: TeamTheme, persist: boolean): void {
    currentTheme = theme
    deps.root.dataset.theme = theme
    deps.lightButton.setAttribute('aria-pressed', String(theme === 'light'))
    deps.darkButton.setAttribute('aria-pressed', String(theme === 'dark'))
    if (persist) safeSetStoredTheme(storage, theme)
  }

  return { getTheme, initializeTheme, registerThemeEvents, setTheme }
}

function normalizeTheme(value: string | undefined | null): TeamTheme | undefined {
  return value === 'light' || value === 'dark' ? value : undefined
}

function readStoredTheme(storage: Storage | undefined): TeamTheme | undefined {
  if (!storage) return undefined
  try {
    return normalizeTheme(storage.getItem(THEME_STORAGE_KEY))
  } catch {
    return undefined
  }
}

function safeSetStoredTheme(storage: Storage | undefined, theme: TeamTheme): void {
  if (!storage) return
  try {
    storage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Browser privacy modes can reject localStorage; the visible toggle still works.
  }
}

function safeLocalStorage(): Storage | undefined {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}
