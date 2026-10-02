import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Relative base: the preview host serves this bundle from a deep, generated path
// prefix, so absolute /assets/... URLs 404 and the app renders as a blank page.
export default defineConfig({
  base: './',
  plugins: [vue()],
  build: { outDir: 'dist', sourcemap: true },
})
