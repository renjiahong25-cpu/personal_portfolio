import { setTimeout as sleep } from 'node:timers/promises'

// vite-node resolves dynamic imports relative to CWD (project root), so use CWD-relative paths.
const DEFAULT_TARGETS = [
  'src/background/controlHandlers.test.ts',
  'src/background/controlHandlers.ts',
  'src/background/runtimeClient.ts',
  'src/group/store.ts',
  'src/shared/localControlProtocol.ts',
  'src/shared/i18n.ts',
  'src/shared/logger.ts',
]

const cliTargets = process.argv.slice(2)
const targets = cliTargets.length > 0 ? cliTargets : DEFAULT_TARGETS
const PROBE_TIMEOUT_MS = Number(process.env.IMPORT_PROBE_TIMEOUT_MS ?? 12000)

async function probe(target: string): Promise<string> {
  const start = Date.now()
  const importPromise = import(target).then(
    () => `OK   ${target} (${Date.now() - start}ms)`,
    (error: unknown) => `ERR  ${target}: ${error instanceof Error ? error.message : String(error)}`,
  )
  const timedOut = await Promise.race([
    importPromise.then(() => false),
    sleep(PROBE_TIMEOUT_MS).then(() => true),
  ])
  if (timedOut) return `HANG ${target} (import did not resolve in ${PROBE_TIMEOUT_MS}ms)`
  return importPromise
}

const results = await Promise.all(targets.map(probe))
for (const line of results) console.log(line)

const hangCount = results.filter(line => line.startsWith('HANG')).length
process.exit(hangCount > 0 ? 1 : 0)
