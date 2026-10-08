// Generates a VAPID key pair and writes it straight into the two env files,
// printing variable NAMES only, never a value:
//
//   apps/web/.env.local  NEXT_PUBLIC_VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT
//   .env (repo root)     VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT  (worker)
//
// Refuses if either file already holds a VAPID key: rotating keys orphans every
// existing push subscription (each is bound to the public key it was made
// with), so it must be a deliberate act:   pnpm gen:vapid -- --force
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import webpush from "web-push";

const SUBJECT = "mailto:duckfanboy@gmail.com";
const at = (p) => fileURLToPath(new URL(p, import.meta.url));
const WEB = at("../.env.local");
const ROOT = at("../../../.env");
const force = process.argv.includes("--force");

function hasVapid(file) {
  return existsSync(file) && /^\s*(NEXT_PUBLIC_)?VAPID_(PUBLIC|PRIVATE)_KEY=\S/m.test(readFileSync(file, "utf8"));
}

/** Set NAME=value lines in place, appending any that are missing. */
function upsert(file, entries) {
  let text = existsSync(file) ? readFileSync(file, "utf8") : "";
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  for (const [name, value] of Object.entries(entries)) {
    const line = `${name}=${value}`;
    const re = new RegExp(`^\\s*#?\\s*${name}=.*$`, "m");
    if (re.test(text)) {
      text = text.replace(re, line);
    } else {
      if (text && !text.endsWith(eol)) text += eol;
      text += line + eol;
    }
  }
  writeFileSync(file, text);
}

if (!existsSync(ROOT)) {
  console.error("No .env at the repository root. Create it from .env.example first.");
  process.exit(1);
}
for (const file of [WEB, ROOT]) {
  if (hasVapid(file) && !force) {
    console.error(
      `${file} already has a VAPID key. Rotating orphans every push subscription; rerun with --force only if that is the intent.`,
    );
    process.exit(1);
  }
}

const { publicKey, privateKey } = webpush.generateVAPIDKeys();

upsert(WEB, {
  NEXT_PUBLIC_VAPID_PUBLIC_KEY: publicKey,
  VAPID_PRIVATE_KEY: privateKey,
  VAPID_SUBJECT: SUBJECT,
});
upsert(ROOT, {
  VAPID_PUBLIC_KEY: publicKey,
  VAPID_PRIVATE_KEY: privateKey,
  VAPID_SUBJECT: SUBJECT,
});

console.log("apps/web/.env.local: NEXT_PUBLIC_VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT written");
console.log(".env (root):         VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT written");
console.log("Values not printed. Copy them from those files into Vercel and Railway.");
