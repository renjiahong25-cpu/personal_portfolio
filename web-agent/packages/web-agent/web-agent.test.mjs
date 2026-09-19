import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { buildCliRequest, help } from './web-agent.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))

describe('web-agent command builder', () => {
  it('builds doctor and daemon commands', () => {
    expect(buildCliRequest(['doctor'])).toEqual({ kind: 'doctor' })
    expect(buildCliRequest(['daemon', 'status'])).toEqual({ kind: 'daemon-status' })
    expect(buildCliRequest(['daemon', 'stop'])).toEqual({ kind: 'daemon-stop' })
  })

  it('builds chat, role, task, and run control commands', () => {
    expect(buildCliRequest(['chat', 'list'])).toMatchObject({
      kind: 'command',
      command: { action: 'chat.list' },
    })
    expect(buildCliRequest(['chat', 'create', '--name', '评审群', '--mode', 'independent'])).toMatchObject({
      kind: 'command',
      command: { action: 'chat.create', payload: { name: '评审群', mode: 'independent' } },
    })
    expect(buildCliRequest(['role', 'batch-add', '--chat', 'chat-1', '--file', 'roles.json'])).toMatchObject({
      kind: 'file-command',
      action: 'roles.batchAdd',
      file: 'roles.json',
      decoratePayload: expect.any(Function),
    })
    expect(buildCliRequest(['task', 'post', '--chat', 'chat-1', '--target', 'all', '--content', '请评估'])).toMatchObject({
      kind: 'command',
      command: { action: 'task.post', payload: { chatId: 'chat-1', target: 'all', content: '请评估' } },
    })
    expect(buildCliRequest(['run', 'create-and-post', '--file', 'task.json', '--wait'])).toMatchObject({
      kind: 'file-command',
      action: 'run.createAndPost',
      file: 'task.json',
      wait: true,
    })
    const withOpts = buildCliRequest(['run', 'create-and-post', '--file', 'task.json', '--wait', '--download-dir', './output', '--name-prefix', 'cat'])
    expect(withOpts).toMatchObject({
      kind: 'file-command',
      action: 'run.createAndPost',
      file: 'task.json',
      wait: true,
    })
    const decorated = withOpts.decoratePayload({ options: {} })
    expect(decorated.options.downloadDir).toBe('./output')
    expect(decorated.options.namePrefix).toBe('cat')
  })

  it('uses the web-agent name in help output', () => {
    expect(help()).toEqual({
      ok: true,
      commands: [
        'web-agent doctor',
        'web-agent daemon start|status|stop|restart|logs',
        'web-agent chat list|get|create|activate|initialize',
        'web-agent role batch-add --chat <chatId> --file roles.json',
        'web-agent task post|wait|read|stop',
        'web-agent run create-and-post --file task.json --wait [--download-dir <path>] [--name-prefix <prefix>]',
      ],
    })
  })

  it('runs when invoked through an npm-style bin symlink', () => {
    const tempDir = mkdtempSync(join(tmpdir(), 'web-agent-bin-'))
    const binPath = join(tempDir, 'web-agent')
    symlinkSync(resolve(__dirname, 'web-agent.mjs'), binPath)

    try {
      const result = spawnSync(binPath, ['help'], { encoding: 'utf8' })

      expect(result.status).toBe(0)
      expect(result.stdout).toContain('web-agent doctor')
    } finally {
      rmSync(tempDir, { recursive: true, force: true })
    }
  })
})
