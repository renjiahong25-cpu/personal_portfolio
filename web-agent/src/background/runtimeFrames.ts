import type { RuntimeFrameAddressKey, RuntimeFrameBinding, RuntimeFrameBindingKey } from '../group/types'

export function roleBindingKey(chatId: string, roleId: string): RuntimeFrameBindingKey {
  return `${chatId}:${roleId}`
}

export function frameAddressKey(tabId: number, frameId: number): RuntimeFrameAddressKey {
  return `${tabId}:${frameId}`
}

export interface RuntimeFrameRegistry {
  bind(binding: RuntimeFrameBinding): RuntimeFrameBinding
  getByRole(chatId: string, roleId: string): RuntimeFrameBinding | undefined
  getByAddress(tabId: number, frameId: number): RuntimeFrameBinding | undefined
  markReady(chatId: string, roleId: string, ready: boolean, lastSeenAt: number): RuntimeFrameBinding | undefined
  removeRole(chatId: string, roleId: string): void
  removeAddress(tabId: number, frameId: number): void
  removeTab(tabId: number): RuntimeFrameBinding[]
  list(): RuntimeFrameBinding[]
}

export function createRuntimeFrameRegistry(): RuntimeFrameRegistry {
  const bindingByRoleKey = new Map<RuntimeFrameBindingKey, RuntimeFrameBinding>()
  const roleKeyByFrameAddress = new Map<RuntimeFrameAddressKey, RuntimeFrameBindingKey>()

  function removeRoleKey(key: RuntimeFrameBindingKey): void {
    const existing = bindingByRoleKey.get(key)
    if (!existing) return

    roleKeyByFrameAddress.delete(frameAddressKey(existing.tabId, existing.frameId))
    bindingByRoleKey.delete(key)
  }

  return {
    bind(binding) {
      const roleKey = roleBindingKey(binding.chatId, binding.roleId)
      const addressKey = frameAddressKey(binding.tabId, binding.frameId)
      const previousRoleKey = roleKeyByFrameAddress.get(addressKey)

      if (previousRoleKey && previousRoleKey !== roleKey) {
        bindingByRoleKey.delete(previousRoleKey)
      }

      removeRoleKey(roleKey)
      bindingByRoleKey.set(roleKey, { ...binding })
      roleKeyByFrameAddress.set(addressKey, roleKey)
      return bindingByRoleKey.get(roleKey)!
    },

    getByRole(chatId, roleId) {
      return bindingByRoleKey.get(roleBindingKey(chatId, roleId))
    },

    getByAddress(tabId, frameId) {
      const key = roleKeyByFrameAddress.get(frameAddressKey(tabId, frameId))
      return key ? bindingByRoleKey.get(key) : undefined
    },

    markReady(chatId, roleId, ready, lastSeenAt) {
      const binding = bindingByRoleKey.get(roleBindingKey(chatId, roleId))
      if (!binding) return undefined

      binding.ready = ready
      binding.lastSeenAt = lastSeenAt
      return binding
    },

    removeRole(chatId, roleId) {
      removeRoleKey(roleBindingKey(chatId, roleId))
    },

    removeAddress(tabId, frameId) {
      const addressKey = frameAddressKey(tabId, frameId)
      const roleKey = roleKeyByFrameAddress.get(addressKey)
      if (!roleKey) return

      roleKeyByFrameAddress.delete(addressKey)
      bindingByRoleKey.delete(roleKey)
    },

    removeTab(tabId) {
      const removed: RuntimeFrameBinding[] = []
      for (const binding of [...bindingByRoleKey.values()]) {
        if (binding.tabId !== tabId) continue
        removed.push(binding)
        removeRoleKey(roleBindingKey(binding.chatId, binding.roleId))
      }
      return removed
    },

    list() {
      return [...bindingByRoleKey.values()]
    },
  }
}
