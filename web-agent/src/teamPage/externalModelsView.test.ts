// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'
import { createDefaultStore } from '../group/store'
import { createExternalModelsView } from './externalModelsView'

describe('external models view', () => {
  it('adds a test action for saved external models', async () => {
    const store = createDefaultStore()
    store.settings.language = 'zh-CN'
    store.settings.externalModelOrder = ['external-model-1']
    store.settings.externalModelsById['external-model-1'] = {
      id: 'external-model-1',
      name: '本地模型',
      format: 'openai',
      baseUrl: 'https://api.example.test/v1',
      apiKey: 'sk-test',
      modelName: 'local-chat-model',
      createdAt: 1,
      updatedAt: 1,
    }
    const runCommand = vi.fn(async () => undefined)
    const testExternalModel = vi.fn(async () => undefined)
    const showError = vi.fn()
    document.body.innerHTML = `
      <button id="settings-button"></button>
      <div id="settings-menu"></div>
      <button id="open-external-models"></button>
      <button id="close-external-models"></button>
      <div id="external-models-modal"></div>
      <div id="external-models-list"></div>
      <form id="external-model-form"></form>
      <input id="external-model-id" />
      <input id="external-model-name" />
      <select id="external-model-format"><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select>
      <input id="external-model-base-url" />
      <input id="external-model-api-key" />
      <input id="external-model-model-name" />
      <button id="reset-external-model-form"></button>
    `

    const view = createExternalModelsView({
      getStore: () => store,
      settingsButtonEl: document.querySelector<HTMLButtonElement>('#settings-button')!,
      settingsMenuEl: document.querySelector<HTMLElement>('#settings-menu')!,
      openExternalModelsEl: document.querySelector<HTMLButtonElement>('#open-external-models')!,
      closeExternalModelsEl: document.querySelector<HTMLButtonElement>('#close-external-models')!,
      externalModelsModalEl: document.querySelector<HTMLElement>('#external-models-modal')!,
      externalModelsListEl: document.querySelector<HTMLElement>('#external-models-list')!,
      externalModelFormEl: document.querySelector<HTMLFormElement>('#external-model-form')!,
      externalModelIdEl: document.querySelector<HTMLInputElement>('#external-model-id')!,
      externalModelNameEl: document.querySelector<HTMLInputElement>('#external-model-name')!,
      externalModelFormatEl: document.querySelector<HTMLSelectElement>('#external-model-format')!,
      externalModelBaseUrlEl: document.querySelector<HTMLInputElement>('#external-model-base-url')!,
      externalModelApiKeyEl: document.querySelector<HTMLInputElement>('#external-model-api-key')!,
      externalModelModelNameEl: document.querySelector<HTMLInputElement>('#external-model-model-name')!,
      resetExternalModelFormEl: document.querySelector<HTMLButtonElement>('#reset-external-model-form')!,
      runCommand,
      testExternalModel,
      showError,
    })

    view.renderExternalModels()
    const testButton = [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '测试')!
    expect(testButton).toBeTruthy()

    testButton.click()
    expect(testButton.disabled).toBe(true)
    expect(testButton.textContent).toBe('测试中')
    await Promise.resolve()

    expect(testExternalModel).toHaveBeenCalledWith('external-model-1')
    expect(runCommand).not.toHaveBeenCalled()
    expect(showError).not.toHaveBeenCalled()
  })

  it('renders external model actions in English mode', () => {
    const store = createDefaultStore()
    store.settings.language = 'en'
    store.settings.externalModelOrder = ['external-model-1']
    store.settings.externalModelsById['external-model-1'] = {
      id: 'external-model-1',
      name: 'Local model',
      format: 'openai',
      baseUrl: 'https://api.example.test/v1',
      apiKey: 'sk-test',
      modelName: 'local-chat-model',
      createdAt: 1,
      updatedAt: 1,
    }
    document.body.innerHTML = `
      <button id="settings-button"></button>
      <div id="settings-menu"></div>
      <button id="open-external-models"></button>
      <button id="close-external-models"></button>
      <div id="external-models-modal"></div>
      <div id="external-models-list"></div>
      <form id="external-model-form"></form>
      <input id="external-model-id" />
      <input id="external-model-name" />
      <select id="external-model-format"><option value="openai">OpenAI</option></select>
      <input id="external-model-base-url" />
      <input id="external-model-api-key" />
      <input id="external-model-model-name" />
      <button id="reset-external-model-form"></button>
    `

    const view = createExternalModelsView({
      getStore: () => store,
      settingsButtonEl: document.querySelector<HTMLButtonElement>('#settings-button')!,
      settingsMenuEl: document.querySelector<HTMLElement>('#settings-menu')!,
      openExternalModelsEl: document.querySelector<HTMLButtonElement>('#open-external-models')!,
      closeExternalModelsEl: document.querySelector<HTMLButtonElement>('#close-external-models')!,
      externalModelsModalEl: document.querySelector<HTMLElement>('#external-models-modal')!,
      externalModelsListEl: document.querySelector<HTMLElement>('#external-models-list')!,
      externalModelFormEl: document.querySelector<HTMLFormElement>('#external-model-form')!,
      externalModelIdEl: document.querySelector<HTMLInputElement>('#external-model-id')!,
      externalModelNameEl: document.querySelector<HTMLInputElement>('#external-model-name')!,
      externalModelFormatEl: document.querySelector<HTMLSelectElement>('#external-model-format')!,
      externalModelBaseUrlEl: document.querySelector<HTMLInputElement>('#external-model-base-url')!,
      externalModelApiKeyEl: document.querySelector<HTMLInputElement>('#external-model-api-key')!,
      externalModelModelNameEl: document.querySelector<HTMLInputElement>('#external-model-model-name')!,
      resetExternalModelFormEl: document.querySelector<HTMLButtonElement>('#reset-external-model-form')!,
      runCommand: vi.fn(async () => undefined),
      testExternalModel: vi.fn(async () => undefined),
      showError: vi.fn(),
    })

    view.renderExternalModels()

    expect(document.querySelector('#external-models-list')?.textContent).toContain('Test')
    expect(document.querySelector('#external-models-list')?.textContent).toContain('Edit')
    expect(document.querySelector('#external-models-list')?.textContent).toContain('Delete')
    expect(document.querySelector('#external-models-list')?.textContent).not.toContain('测试')
  })
})
