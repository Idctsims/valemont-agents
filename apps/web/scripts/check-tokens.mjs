// Fails if anything under src/ (except src/styles/tokens.css) bypasses the
// design tokens. Run: pnpm check:tokens (also part of pnpm lint).
//
//   - raw hex colours (#fff, #1c2228, #1c2228cc)
//   - colour functions (rgb(), rgba(), hsl(), oklch(), ...)
//   - Tailwind arbitrary values and properties: bg-[#..], text-[13px],
//     w-[37px], bg-(--x), [mask-type:alpha]
//   - Tailwind default greys: gray-*, slate-*, zinc-*, neutral-*, stone-*
//
// A rule that needs an exception belongs in tokens.css as a token or an
// @utility, not as an escape hatch here.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = join(root, "src");
const EXEMPT = new Set([join(src, "styles", "tokens.css")]);
const EXTENSIONS = /\.(tsx?|jsx?|mjs|cjs|css)$/;

export const RULES = [
  {
    name: "raw hex colour",
    re: /(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g,
  },
  {
    name: "colour function",
    re: /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/g,
  },
  {
    name: "Tailwind arbitrary value",
    re: /\b[a-z][\w-]*-\[[^\]\s]+\]/g,
  },
  {
    name: "Tailwind arbitrary CSS variable",
    re: /\b[a-z][\w-]*-\(--[\w-]+\)/g,
  },
  {
    name: "Tailwind arbitrary property",
    re: /(?<![\w\]])\[[a-z-]+:[^\]\s]+\]/g,
  },
  {
    name: "default grey utility",
    re: /\b(?:gray|slate|zinc|neutral|stone)-(?:50|[1-9]00|950)\b/g,
  },
];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : EXTENSIONS.test(p) ? [p] : [];
  });
}

export function scan(text) {
  const hits = [];
  text.split(/\r?\n/).forEach((line, i) => {
    for (const { name, re } of RULES) {
      for (const m of line.matchAll(re)) hits.push({ line: i + 1, rule: name, match: m[0] });
    }
  });
  return hits;
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  let count = 0;
  for (const file of walk(src)) {
    if (EXEMPT.has(file)) continue;
    for (const hit of scan(readFileSync(file, "utf8"))) {
      count++;
      const rel = relative(root, file).split(sep).join("/");
      console.error(`${rel}:${hit.line}  ${hit.rule}: ${hit.match}`);
    }
  }
  if (count) {
    console.error(`\ncheck:tokens: ${count} violation(s). Use a token from src/styles/tokens.css.`);
    process.exit(1);
  }
  console.log("check:tokens: clean.");
}
