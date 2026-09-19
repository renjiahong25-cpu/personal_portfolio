import { describe, expect, it, vi } from 'vitest'
import { waitBeforePromptInput } from './promptDelay'

describe('waitBeforePromptInput', () => {
  it('waits before the role page starts typing into Gemini', async () => {
    vi.useFakeTimers()

    let resolved = false
    const promise = waitBeforePromptInput().then(() => {
      resolved = true
    })

    await vi.advanceTimersByTimeAsync(1499)
    expect(resolved).toBe(false)

    await vi.advanceTimersByTimeAsync(1)
    await promise
    expect(resolved).toBe(true)

    vi.useRealTimers()
  })
})
