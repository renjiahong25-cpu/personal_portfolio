export const PROMPT_INPUT_DELAY_MS = 1500

export function waitBeforePromptInput(delayMs = PROMPT_INPUT_DELAY_MS): Promise<void> {
  return new Promise(resolve => {
    setTimeout(resolve, delayMs)
  })
}
