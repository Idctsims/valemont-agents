// Fails if anything under src/ outside src/lib/ai/ imports Anthropic's SDK or
// the AI SDK's Anthropic provider. Every web Claude call goes through
// src/lib/ai/ (claude.ts), where its cost is written to ai_usage and the
// monthly budget applies (CLAUDE.md §6, the TypeScript twin of core/ai.py).
// Run: pnpm check:ai-import (also part of pnpm lint).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = join(root, "src");
const ALLOWED = join(src, "lib", "ai") + sep;
const IMPORT = /from\s+["'](@ai-sdk\/anthropic|@anthropic-ai\/sdk)["']|import\(\s*["'](@ai-sdk\/anthropic|@anthropic-ai\/sdk)["']\s*\)|require\(\s*["'](@ai-sdk\/anthropic|@anthropic-ai\/sdk)["']\s*\)/;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|jsx?|mjs|cjs)$/.test(p) ? [p] : [];
  });
}

const problems = [];
for (const file of walk(src)) {
  if (file.startsWith(ALLOWED)) continue;
  const rel = relative(root, file).split(sep).join("/");
  readFileSync(file, "utf8")
    .split(/\r?\n/)
    .forEach((line, i) => {
      if (IMPORT.test(line)) problems.push(`${rel}:${i + 1}: Anthropic imported outside src/lib/ai/`);
    });
}

if (problems.length) {
  for (const p of problems) console.error(p);
  console.error(`\ncheck:ai-import: ${problems.length} violation(s). Call Claude through src/lib/ai/claude.ts.`);
  process.exit(1);
}
console.log("check:ai-import: clean (src/lib/ai/ only).");
