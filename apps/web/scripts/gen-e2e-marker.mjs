// Generates E2E_TEST_MARKER (32 random bytes, base64url) into
// apps/web/.env.local without printing it, and prints its SHA-256, which is
// what the database stores (db/027 e2e_markers) and is safe to show: the
// marker is 256 random bits, so the hash cannot be reversed.
//
// The marker lets the LOCAL test build write is_test bankroll entries
// (db/027). It must NEVER be set on Vercel: production refuses any request
// carrying the marker header (src/lib/capital/marker.ts).
//
// Refuses to replace an existing marker without --force: the database keeps
// the old hash until a new e2e_markers row is pasted.
//   pnpm gen:e2e-marker            (-- --force to rotate)
import { createHash, randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const file = fileURLToPath(new URL("../.env.local", import.meta.url));
const text = existsSync(file) ? readFileSync(file, "utf8") : "";
const has = /^\s*E2E_TEST_MARKER=\S/m.test(text);
if (has && !process.argv.includes("--force")) {
  const current = text.match(/^\s*E2E_TEST_MARKER=(\S+)/m)[1];
  console.log(`apps/web/.env.local already has E2E_TEST_MARKER. sha256: ${createHash("sha256").update(current).digest("hex")}`);
  console.log("Rotate with --force, then paste an e2e_markers row with the new hash.");
  process.exit(0);
}

const marker = randomBytes(32).toString("base64url");
const line = `E2E_TEST_MARKER=${marker}`;
const eol = text.includes("\r\n") ? "\r\n" : "\n";
const next = /^\s*#?\s*E2E_TEST_MARKER=.*$/m.test(text)
  ? text.replace(/^\s*#?\s*E2E_TEST_MARKER=.*$/m, line)
  : `${text}${text && !text.endsWith(eol) ? eol : ""}${line}${eol}`;
writeFileSync(file, next);
console.log("apps/web/.env.local: E2E_TEST_MARKER written (value not printed).");
console.log(`sha256: ${createHash("sha256").update(marker).digest("hex")}`);
