import { build } from 'vite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const entry = path.join(root, 'probe-entry.ts')
const outDir = path.join(root, 'dist-probe')

await build({
  configFile: false,
  logLevel: 'error',
  build: {
    outDir,
    emptyOutDir: true,
    lib: {
      entry,
      name: 'WebAgentProbe',
      formats: ['iife'],
      fileName: () => 'probe.js',
    },
    minify: false,
    sourcemap: false,
  },
})

console.log('PROBE_BUILD_DONE', path.join(outDir, 'probe.js'))