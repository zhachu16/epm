import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` proxies the API to a running `fm serve --no-auth` on port 8765.
const target = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.EPM_SERVER ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target, ws: true, changeOrigin: false },
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
