import { defineConfig, devices } from "@playwright/test";

// The tests drive the built UI (`npm run build`) served by `fm serve` on a scratch copy of the
// LBO example. EPM_PYTHON names a Python with epm installed (default: python3 on PATH).
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const port = 8899;
const python = env.EPM_PYTHON ?? "python3";

export default defineConfig({
  testDir: "e2e",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  forbidOnly: Boolean(env.CI),
  reporter: env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
  webServer: {
    command: `${python} e2e/serve.py ${port}`,
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
});
