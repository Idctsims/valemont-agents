// WCAG AA contrast check for the colour pairs the UI actually uses.
// Reads src/styles/tokens.css directly, so there is no second copy of any
// value. Exits 1 if any pair fails. Run: pnpm check:contrast
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const css = readFileSync(
  fileURLToPath(new URL("../src/styles/tokens.css", import.meta.url)),
  "utf8",
);

function block(theme) {
  const re = new RegExp(`\\[data-theme="${theme}"\\]\\s*\\{([^}]*)\\}`);
  const m = css.match(re);
  if (!m) throw new Error(`no [data-theme="${theme}"] block in tokens.css`);
  const vars = {};
  for (const [, name, hex] of m[1].matchAll(/--vm-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    vars[name] = hex;
  }
  return vars;
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background, minimum]. 4.5 = AA body text.
const surfaces = ["bg", "surface", "surface-2"];
const pairs = [
  ...surfaces.flatMap((s) => [
    ["text", s, 4.5],
    ["text-muted", s, 4.5],
    ["accent", s, 4.5],
    ["hit", s, 4.5],
    ["on-pace", s, 4.5],
    ["danger", s, 4.5],
    ["dead", s, 4.5],
    ["live", s, 4.5],
  ]),
  ...["hit", "on-pace", "danger", "dead", "live"].map((s) => [s, `${s}-fill`, 4.5]),
  ["text", "surface-3", 4.5],
  ["on-accent", "accent", 4.5],
  ["on-accent-strong", "accent-strong", 4.5],
  ["paper-fg", "paper-bg", 4.5],
];

// /betting's own palette (.betting-theme), merged over its theme: the same
// pairs, plus the accent as text and the slate band and the picked cell.
function bettingBlock(theme) {
  const re = new RegExp(`\\[data-theme="${theme}"\\]:has\\(\\.betting-theme\\)\\s*\\{([^}]*)\\}`);
  const m = css.match(re);
  if (!m) throw new Error(`no [data-theme="${theme}"]:has(.betting-theme) block in tokens.css`);
  const vars = { ...block(theme) };
  for (const [, name, hex] of m[1].matchAll(/--vm-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) vars[name] = hex;
  return vars;
}
// The accent there is a FILL (night: a deep green under white text); its
// text and outline colour is accent-ink, so "accent as text" is accent-ink's
// pair, and no betting file may use text-accent (checked below).
const bettingPairs = [
  ...pairs.filter(([fg]) => fg !== "accent"),
  ...[...surfaces, "band", "pick"].map((s) => ["accent-ink", s, 4.5]),
  ["text", "band", 4.5],
  ["text-muted", "band", 4.5],
  ["text", "pick", 4.5],
  ["text-muted", "pick", 4.5],
];

let failed = 0;
const themes = [
  ...["night", "day"].map((t) => ({ theme: t.toUpperCase(), v: block(t), set: pairs })),
  ...["night", "day"].map((t) => ({ theme: `BETTING ${t.toUpperCase()}`, v: bettingBlock(t), set: bettingPairs })),
];
for (const { theme, v, set } of themes) {
  console.log(`\n${theme}`);
  for (const [fg, bg, min] of set) {
    if (!v[fg] || !v[bg]) throw new Error(`${theme}: missing --vm-${fg} or --vm-${bg}`);
    const r = ratio(v[fg], v[bg]);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(
      `  ${ok ? "pass" : "FAIL"}  ${r.toFixed(2).padStart(5)}:1  ${fg} on ${bg}  (${v[fg]} / ${v[bg]})`,
    );
  }
}

// No betting file uses the fill accent as text (text-accent, not text-accent-ink).
{
  const { readdirSync, statSync } = await import("node:fs");
  const { join } = await import("node:path");
  const roots = ["../src/components/betting", "../src/app/(app)/betting"].map((r) => fileURLToPath(new URL(r, import.meta.url)));
  const files = [];
  const walk = (d) => { for (const n of readdirSync(d)) { const p = join(d, n); if (statSync(p).isDirectory()) walk(p); else if (/\.tsx?$/.test(n)) files.push(p); } };
  for (const r of roots) walk(r);
  const bad = files.filter((f) => /\btext-accent(?![-\w])/.test(readFileSync(f, "utf8")));
  console.log(`\nBETTING FILES: ${bad.length === 0 ? "no text-accent" : "text-accent in " + bad.join(", ")}`);
  if (bad.length) failed += bad.length;
}

if (failed) {
  console.error(`\n${failed} pair(s) below WCAG AA.`);
  process.exit(1);
}
console.log("\nAll pairs meet WCAG AA.");
