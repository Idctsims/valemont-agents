// Fails if TMDB_READ_TOKEN is read anywhere under src/ outside src/lib/tmdb/,
// or if a file there that reads it is not a server-only module, or if
// anything reaches for a NEXT_PUBLIC_ copy (which would ship it to browsers).
// Run: pnpm check:tmdb-token (also part of pnpm lint). Chat 4 Phase 3, db/051.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = join(root, "src");
const ALLOWED_DIR = join(src, "lib", "tmdb") + sep;
const KEY = /TMDB_READ_TOKEN/;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|jsx?|mjs|cjs)$/.test(p) ? [p] : [];
  });
}

const problems = [];
let readers = 0;
for (const file of walk(src)) {
  const text = readFileSync(file, "utf8");
  const rel = relative(root, file).split(sep).join("/");
  if (/NEXT_PUBLIC_TMDB/.test(text)) problems.push(`${rel}: a NEXT_PUBLIC_ TMDB token would ship to the browser`);
  if (!KEY.test(text)) continue;
  if (!file.startsWith(ALLOWED_DIR)) {
    text.split(/\r?\n/).forEach((line, i) => {
      if (KEY.test(line)) problems.push(`${rel}:${i + 1}: TMDB_READ_TOKEN referenced outside src/lib/tmdb/`);
    });
    continue;
  }
  readers++;
  if (/^\s*["']use client["']/m.test(text)) problems.push(`${rel}: reads TMDB_READ_TOKEN, so it must stay a server module`);
  if (!/^import ["']server-only["'];/m.test(text)) problems.push(`${rel}: reads TMDB_READ_TOKEN, so it must import "server-only"`);
}

if (problems.length) {
  for (const p of problems) console.error(p);
  console.error(`\ncheck:tmdb-token: ${problems.length} violation(s).`);
  process.exit(1);
}
console.log(`check:tmdb-token: clean (${readers} server-only reader(s) under src/lib/tmdb/).`);
