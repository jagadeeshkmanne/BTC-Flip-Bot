import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
import path from 'path';

// Vite config: builds to ../static/bots so server.py can serve at /bots/
// base: '/bots/' so all asset URLs in built HTML/JS are absolute under /bots/
export default defineConfig({
  plugins: [preact()],
  base: '/bots/',
  resolve: {
    alias: {
      // React-aliased to Preact so libs like @tanstack work seamlessly
      'react': 'preact/compat',
      'react-dom': 'preact/compat',
      'react-dom/test-utils': 'preact/test-utils',
      'react/jsx-runtime': 'preact/jsx-runtime',
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../static/bots',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2020',
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          'lightweight-charts': ['lightweight-charts'],
          'tanstack': ['@tanstack/react-query', '@tanstack/react-table', '@tanstack/react-virtual'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://34.14.124.215:8888',
    },
  },
});
