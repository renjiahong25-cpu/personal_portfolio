import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const packageRoot = import.meta.dirname
const root = resolve(packageRoot, '../..')

describe('web-agent install configuration', () => {
  it('keeps the extension project private and delegates publish metadata to the CLI package', () => {
    const rootPkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'))

    expect(rootPkg.private).toBe(true)
    expect(rootPkg.bin).toBeUndefined()
    expect(rootPkg.files).toBeUndefined()
    expect(rootPkg.scripts['web-agent']).toBe('node packages/web-agent/web-agent.mjs')
  })

  it('exposes web-agent from a lightweight publishable package', () => {
    const pkg = JSON.parse(readFileSync(resolve(packageRoot, 'package.json'), 'utf8'))

    expect(pkg).toMatchObject({
      name: '@afumu/web-agent',
      version: expect.any(String),
      type: 'module',
      private: false,
      bin: {
        'web-agent': 'web-agent.mjs',
      },
      engines: {
        node: '>=18',
      },
      publishConfig: {
        access: 'public',
      },
    })
    expect(pkg.files).toEqual(expect.arrayContaining([
      'web-agent.mjs',
      'web-agent-daemon.mjs',
      'skills',
    ]))
  })

  it('documents the installed web-agent command in the skill', () => {
    const skill = readFileSync(resolve(packageRoot, 'skills/web-agent-control/SKILL.md'), 'utf8')

    expect(skill).toContain('web-agent daemon start')
    expect(skill).toContain('web-agent doctor')
    expect(skill).not.toContain('npm run web-agent --')
  })
})
