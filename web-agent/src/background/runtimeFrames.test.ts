import { describe, expect, it } from 'vitest'
import { createRuntimeFrameRegistry, frameAddressKey, roleBindingKey } from './runtimeFrames'

function binding(chatId: string, roleId: string, tabId: number, frameId: number) {
  return { chatId, roleId, tabId, frameId, ready: false, lastSeenAt: 1 }
}

describe('runtime frame registry', () => {
  it('indexes bindings by chat role and runtime frame address', () => {
    const registry = createRuntimeFrameRegistry()
    const first = registry.bind(binding('chat-1', 'role-1', 10, 3))

    expect(roleBindingKey('chat-1', 'role-1')).toBe('chat-1:role-1')
    expect(frameAddressKey(10, 3)).toBe('10:3')
    expect(registry.getByRole('chat-1', 'role-1')).toBe(first)
    expect(registry.getByAddress(10, 3)).toBe(first)
  })

  it('replaces stale role and address bindings when frames are rebound', () => {
    const registry = createRuntimeFrameRegistry()

    registry.bind(binding('chat-1', 'role-1', 10, 3))
    const reboundRole = registry.bind(binding('chat-1', 'role-1', 10, 4))
    expect(registry.getByAddress(10, 3)).toBeUndefined()
    expect(registry.getByAddress(10, 4)).toBe(reboundRole)

    const reboundAddress = registry.bind(binding('chat-1', 'role-2', 10, 4))
    expect(registry.getByRole('chat-1', 'role-1')).toBeUndefined()
    expect(registry.getByRole('chat-1', 'role-2')).toBe(reboundAddress)
    expect(registry.getByAddress(10, 4)).toBe(reboundAddress)
  })

  it('marks ready state and removes bindings by role, address, or tab', () => {
    const registry = createRuntimeFrameRegistry()
    registry.bind(binding('chat-1', 'role-1', 10, 3))
    registry.bind(binding('chat-1', 'role-2', 10, 4))
    registry.bind(binding('chat-2', 'role-1', 11, 1))

    expect(registry.markReady('chat-1', 'role-1', true, 99)).toMatchObject({ ready: true, lastSeenAt: 99 })

    registry.removeAddress(10, 4)
    expect(registry.getByRole('chat-1', 'role-2')).toBeUndefined()

    const removed = registry.removeTab(10)
    expect(removed).toEqual([expect.objectContaining({ chatId: 'chat-1', roleId: 'role-1' })])
    expect(registry.list()).toEqual([expect.objectContaining({ chatId: 'chat-2', roleId: 'role-1' })])

    registry.removeRole('chat-2', 'role-1')
    expect(registry.list()).toEqual([])
  })
})
