import type { GroupChat, GroupRole } from '../group/types'
import { isUnavailableRolesError, shouldAutoReconnectRole } from './chatExperience'

export interface SendWithReconnectDependencies {
  reconnectRolesForSend(chat: GroupChat, roles: GroupRole[]): Promise<void>
  runCommand(type: string, payload?: Record<string, unknown>): Promise<void>
}

export interface SendWithReconnectInput {
  chat: GroupChat
  roles: GroupRole[]
  type: string
  payload?: Record<string, unknown>
  preconnectAll?: boolean
}

export async function runCommandWithReconnect(deps: SendWithReconnectDependencies, input: SendWithReconnectInput): Promise<void> {
  const initialReconnectRoles = input.preconnectAll
    ? input.roles
    : input.roles.filter(role => role.status !== 'ready' && shouldAutoReconnectRole(role))
  if (initialReconnectRoles.length > 0) await deps.reconnectRolesForSend(input.chat, initialReconnectRoles)
  await runCommandAfterReconnect(deps, input, true)
}

async function runCommandAfterReconnect(deps: SendWithReconnectDependencies, input: SendWithReconnectInput, retryOnUnavailable: boolean): Promise<void> {
  try {
    await deps.runCommand(input.type, input.payload)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (!retryOnUnavailable || !isUnavailableRolesError(message)) throw error
    const reconnectableRoles = input.roles.filter(role => role.status === 'ready' || shouldAutoReconnectRole(role))
    if (reconnectableRoles.length === 0) throw error
    await deps.reconnectRolesForSend(input.chat, reconnectableRoles)
    await runCommandAfterReconnect(deps, input, false)
  }
}
