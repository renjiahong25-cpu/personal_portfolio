// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { createThemeController, THEME_STORAGE_KEY, type TeamTheme } from './themeController'

describe('theme controller', () => {
  it('initializes the saved light theme and active button states', () => {
    const harness = createHarness('light')

    harness.controller.initializeTheme()

    expect(harness.root.dataset.theme).toBe('light')
    expect(harness.lightButton.getAttribute('aria-pressed')).toBe('true')
    expect(harness.darkButton.getAttribute('aria-pressed')).toBe('false')
    expect(harness.controller.getTheme()).toBe('light')
  })

  it('switches theme from the buttons and saves the selection', () => {
    const harness = createHarness('dark')
    harness.controller.initializeTheme()

    harness.lightButton.click()

    expect(harness.root.dataset.theme).toBe('light')
    expect(harness.storage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(harness.lightButton.getAttribute('aria-pressed')).toBe('true')
    expect(harness.darkButton.getAttribute('aria-pressed')).toBe('false')

    harness.darkButton.click()

    expect(harness.root.dataset.theme).toBe('dark')
    expect(harness.storage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(harness.lightButton.getAttribute('aria-pressed')).toBe('false')
    expect(harness.darkButton.getAttribute('aria-pressed')).toBe('true')
  })
})

function createHarness(savedTheme: TeamTheme) {
  const root = document.createElement('html')
  const lightButton = document.createElement('button')
  const darkButton = document.createElement('button')
  const storage = createStorage()
  storage.setItem(THEME_STORAGE_KEY, savedTheme)
  const controller = createThemeController({
    root,
    lightButton,
    darkButton,
    storage,
  })
  controller.registerThemeEvents()
  return { controller, darkButton, lightButton, root, storage }
}

function createStorage(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear() {
      values.clear()
    },
    getItem(key: string) {
      return values.get(key) ?? null
    },
    key(index: number) {
      return Array.from(values.keys())[index] ?? null
    },
    removeItem(key: string) {
      values.delete(key)
    },
    setItem(key: string, value: string) {
      values.set(key, value)
    },
  }
}
