// Generates the app icons: a "V" monogram in Instrument Serif, accent glyph
// on the Night background inside a thin wood frame. Colours come from
// src/styles/tokens.css; nothing is restated here.
//
// Renders with Playwright's Chromium (already a dev dependency), so the glyph
// is the real typeface, not a path traced by hand. Fetches the font from
// Google Fonts once per run. Outputs are committed; rerun after a token or
// design change:   pnpm gen:icons
import { mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

import { themeBlock } from "./gen-theme-colors.mjs";

const root = (p) => fileURLToPath(new URL(`../${p}`, import.meta.url));
const night = themeBlock(readFileSync(root("src/styles/tokens.css"), "utf8"), "night");

async function fontDataUri() {
  // A bare User-Agent gets TrueType URLs back from the css2 API.
  const css = await (
    await fetch("https://fonts.googleapis.com/css2?family=Instrument+Serif&display=block", {
      headers: { "User-Agent": "node" },
    })
  ).text();
  const url = css.match(/src:\s*url\(([^)]+)\)/)?.[1];
  if (!url) throw new Error("could not find the Instrument Serif font URL");
  const buf = Buffer.from(await (await fetch(url)).arrayBuffer());
  return `data:font/ttf;base64,${buf.toString("base64")}`;
}

/**
 * kind:
 *   any       full-bleed Night square, wood frame inset 7%
 *   maskable  frame and glyph inside the 80% safe zone (Android crops to a
 *             circle or squircle), so the frame insets to 21%
 *   badge     Android status-bar badge: white glyph on transparent, no frame
 */
function html(font, size, kind) {
  const frameInset = kind === "maskable" ? 0.21 : 0.07;
  const frame = Math.max(1, Math.round(size * (kind === "maskable" ? 0.012 : 0.016)));
  const glyph = size * (kind === "maskable" ? 0.5 : kind === "badge" ? 0.92 : 0.66);
  const bg = kind === "badge" ? "transparent" : night.bg;
  const fg = kind === "badge" ? "#fff" : night.accent;
  return `<!doctype html><html><head><style>
  @font-face { font-family: IS; src: url(${font}); font-display: block; }
  html, body { margin: 0; width: ${size}px; height: ${size}px; background: ${bg}; }
  .frame { position: absolute; inset: ${size * frameInset}px;
           border: ${frame}px solid ${night.wood}; border-radius: ${size * 0.06}px; }
  .v { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
       font-family: IS; font-size: ${glyph}px; line-height: 1; color: ${fg};
       /* Instrument Serif sits high in its em box; nudge the cap to optical centre. */
       padding-top: ${glyph * 0.08}px; box-sizing: border-box; }
  </style></head><body>
  ${kind === "badge" ? "" : '<div class="frame"></div>'}
  <div class="v">V</div></body></html>`;
}

const OUT = [
  { file: "public/icons/icon-192.png", size: 192, kind: "any" },
  { file: "public/icons/icon-512.png", size: 512, kind: "any" },
  { file: "public/icons/maskable-512.png", size: 512, kind: "maskable" },
  { file: "public/icons/badge-96.png", size: 96, kind: "badge" },
  // Next file conventions: <link rel="apple-touch-icon"> and <link rel="icon">.
  { file: "src/app/apple-icon.png", size: 180, kind: "any" },
  { file: "src/app/icon.png", size: 48, kind: "any" },
];

const font = await fontDataUri();
const browser = await chromium.launch();
try {
  mkdirSync(root("public/icons"), { recursive: true });
  for (const { file, size, kind } of OUT) {
    const page = await browser.newPage({ viewport: { width: size, height: size } });
    await page.setContent(html(font, size, kind));
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: root(file), omitBackground: kind === "badge" });
    await page.close();
    console.log(`${file}  ${size}px  ${kind}`);
  }
} finally {
  await browser.close();
}
