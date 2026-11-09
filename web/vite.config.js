import { defineConfig } from 'vite';

export default defineConfig({
  build: { outDir: '../app/static', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/socket.io': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
});
