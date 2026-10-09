import { defineConfig } from "@playwright/test";

// Logic-only specs (*-unit.spec.ts): no browser page and no web server, so
// they run in about a second and need no .env.local. Run: pnpm test:unit
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*-unit.spec.ts",
  outputDir: "./test-results/unit",
  reporter: [["list"]],
});
