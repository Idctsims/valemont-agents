import { spawnSync } from "node:child_process";

import { createSerwistRoute } from "@serwist/turbopack";

// Builds src/app/sw.ts with esbuild and serves it at /serwist/sw.js with
// Service-Worker-Allowed: / (scope "/"). Static at build time.
//
// The revision versions the precached /offline page. VERCEL_GIT_COMMIT_SHA on
// Vercel, `git rev-parse HEAD` locally, a random value as the last resort.
const revision =
  process.env.VERCEL_GIT_COMMIT_SHA ||
  spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf-8" }).stdout?.trim() ||
  crypto.randomUUID();

export const { dynamic, dynamicParams, revalidate, generateStaticParams, GET } = createSerwistRoute({
  swSrc: "src/app/sw.ts",
  additionalPrecacheEntries: [{ url: "/offline", revision }],
  useNativeEsbuild: true,
  // A self-contained classic script: registers on every iOS version that has
  // web push (16.4+), with no module-worker support needed.
  esbuildOptions: { format: "iife" },
});
