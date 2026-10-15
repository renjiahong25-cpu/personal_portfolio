// Use Vite's API to avoid esbuild bundling a config through inaccessible ancestors.
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import config from '../frontend/vite.config.js'

await build({ ...config, configFile: false, root: fileURLToPath(new URL('../frontend', import.meta.url)) })
