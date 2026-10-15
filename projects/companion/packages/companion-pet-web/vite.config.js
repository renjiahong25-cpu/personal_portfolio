import { defineConfig } from 'vite';

// The bundle is served by app/pet3d/assets_server.py from 127.0.0.1 on an ephemeral
// port, so every asset reference has to be relative (no absolute "/assets/..." paths).
export default defineConfig({
  base: './',
  build: {
    target: 'es2020',
    outDir: 'dist',
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/model': 'http://127.0.0.1:8799',
      '/__c03': 'http://127.0.0.1:8799',
    },
  },
});