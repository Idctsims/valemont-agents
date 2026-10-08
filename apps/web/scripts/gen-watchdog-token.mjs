// Generates WATCHDOG_TOKEN (32 random bytes, base64url) into apps/web/.env.local
// without printing it. Copy it from that file into Vercel (Sensitive) and
// into the cron-job.org request header.
//
// Refuses to replace an existing token without --force: the cron job would
// start failing with 401 until its header is updated too.
//   pnpm gen:watchdog-token            (-- --force to rotate)
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const file = fileURLToPath(new URL("../.env.local", import.meta.url));
const text = existsSync(file) ? readFileSync(file, "utf8") : "";
const has = /^\s*WATCHDOG_TOKEN=\S/m.test(text);
if (has && !process.argv.includes("--force")) {
  console.error("apps/web/.env.local already has WATCHDOG_TOKEN. Rotate with --force, then update Vercel and cron-job.org.");
  process.exit(1);
}

const line = `WATCHDOG_TOKEN=${randomBytes(32).toString("base64url")}`;
const eol = text.includes("\r\n") ? "\r\n" : "\n";
const next = /^\s*#?\s*WATCHDOG_TOKEN=.*$/m.test(text)
  ? text.replace(/^\s*#?\s*WATCHDOG_TOKEN=.*$/m, line)
  : `${text}${text && !text.endsWith(eol) ? eol : ""}${line}${eol}`;
writeFileSync(file, next);
console.log("apps/web/.env.local: WATCHDOG_TOKEN written (value not printed).");
