import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` proxies the API to a local service (`python -m service.service --memory`).
// Deployed builds call the service at VITE_API_URL instead.
const target = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.EPM_SERVICE ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 6000,
    rollupOptions: {
      output: {
        manualChunks: {
          monaco: ["monaco-editor"],
          echarts: ["echarts"],
          graph: ["@xyflow/react", "elkjs/lib/elk.bundled.js"],
        },
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
});
