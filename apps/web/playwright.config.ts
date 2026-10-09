import { defineConfig, devices } from "@playwright/test";

// Smoke tests against a local production build (next build + next start),
// reading apps/web/.env.local like the real server. Run from apps/web:
//   pnpm test:e2e
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  // Logic-only specs run under playwright.unit.config.ts (pnpm test:unit).
  testIgnore: "**/*-unit.spec.ts",
  outputDir: "./test-results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    // 390px phone, Chromium engine (no WebKit download needed on Windows).
    { name: "phone", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `pnpm build && pnpm start --port ${PORT}`,
    url: `http://localhost:${PORT}/privacy`,
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
