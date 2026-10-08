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

let failed = 0;
for (const theme of ["night", "day"]) {
  const v = block(theme);
  console.log(`\n${theme.toUpperCase()}`);
  for (const [fg, bg, min] of pairs) {
    if (!v[fg] || !v[bg]) throw new Error(`${theme}: missing --vm-${fg} or --vm-${bg}`);
    const r = ratio(v[fg], v[bg]);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(
      `  ${ok ? "pass" : "FAIL"}  ${r.toFixed(2).padStart(5)}:1  ${fg} on ${bg}  (${v[fg]} / ${v[bg]})`,
    );
  }
}

if (failed) {
  console.error(`\n${failed} pair(s) below WCAG AA.`);
  process.exit(1);
}
console.log("\nAll pairs meet WCAG AA.");
