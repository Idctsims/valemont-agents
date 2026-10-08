// Fails if the Supabase secret key is referenced anywhere under src/ except
// the watchdog route. That key bypasses RLS; one server file may hold it.
// Run: pnpm check:secret-key (also part of pnpm lint).
//
// Also fails if that one file ever becomes a client module, or if anything
// reaches for a NEXT_PUBLIC_ copy of the key, which would ship it to browsers.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = join(root, "src");
const ALLOWED = join(src, "app", "api", "watchdog", "route.ts");
const KEY = /SUPABASE_SECRET_KEY|SUPABASE_SERVICE_ROLE_KEY|service_role/;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|jsx?|mjs|cjs)$/.test(p) ? [p] : [];
  });
}

const problems = [];
for (const file of walk(src)) {
  const text = readFileSync(file, "utf8");
  const rel = relative(root, file).split(sep).join("/");
  if (/NEXT_PUBLIC_SUPABASE_SECRET|NEXT_PUBLIC_SUPABASE_SERVICE/.test(text)) {
    problems.push(`${rel}: a NEXT_PUBLIC_ secret key would ship to the browser`);
  }
  if (file === ALLOWED) {
    if (/^\s*["']use client["']/m.test(text)) problems.push(`${rel}: must stay a server module`);
    continue;
  }
  text.split(/\r?\n/).forEach((line, i) => {
    if (KEY.test(line)) problems.push(`${rel}:${i + 1}: Supabase secret key referenced outside the watchdog route`);
  });
}

if (problems.length) {
  for (const p of problems) console.error(p);
  console.error(`\ncheck:secret-key: ${problems.length} violation(s).`);
  process.exit(1);
}
console.log("check:secret-key: clean (watchdog route only).");
