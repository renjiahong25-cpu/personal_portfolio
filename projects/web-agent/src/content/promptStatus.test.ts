import { describe, expect, it } from 'vitest'
import { promptStatusMessage } from './promptStatus'

describe('promptStatusMessage', () => {
  it('keeps prompt identity on status messages so background does not rely on frame binding', () => {
    expect(promptStatusMessage('sending', 'chat-1', 'role-reviewer')).toEqual({
      type: 'TEAM_ROLE_STATUS',
      status: 'sending',
      chatId: 'chat-1',
      roleId: 'role-reviewer',
    })
  })
})
