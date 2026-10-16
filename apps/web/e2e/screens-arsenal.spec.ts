import { expect, test, type Page } from "@playwright/test";

import {
  addTestArsenalDrop, addTestArsenalRef, addTestArsenalWord, hasOwner, runPrefix, setTheme, signIn, useE2eMarker,
} from "./support/owner";

// Screenshot gate for Chat 5 Phase 1: /arsenal on the REAL data (today's
// drop, the Past drops and Unverified folds open), then a TEST drop with the
// marker (a quote, a Corrected tag, every room) so the card layout is
// checked even before the first real drop. Both themes, phone 390x844 and
// desktop 1440. Written to e2e/screenshots/chat5-p1/ (gitignored). Needs
// db/054.
//   pnpm test:e2e e2e/screens-arsenal.spec.ts --project=phone --workers=1 --output=test-results/screens-arsenal-phone
//   pnpm test:e2e e2e/screens-arsenal.spec.ts --project=desktop --workers=1 --output=test-results/screens-arsenal-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat5-p1";
const THEMES = ["night", "day"] as const;

// A full-page capture paints the fixed dock and the sticky phone header
// wherever the viewport sat, i.e. mid-page (over card 1, inside the
// Unverified fold). Both hidden for the capture only; the app is unchanged.
const HIDE_CHROME = 'nav[aria-label="Dock"], header.sticky { visibility: hidden !important; }';

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = true) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage, style: HIDE_CHROME });
}

// Opens every fold that has rows; an empty one is locked closed (lockWhenEmpty).
async function openFolds(page: Page) {
  for (const id of ["arsenal-past", "arsenal-unverified"]) {
    const button = page.getByTestId(id).getByRole("button").first();
    if (await button.isDisabled()) continue;
    if ((await button.getAttribute("aria-expanded")) === "false") await button.click();
  }
}

test("arsenal screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/arsenal");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/arsenal");
    await expect(page.getByTestId("power-word").or(page.getByTestId("arsenal-empty"))).toBeVisible();
    await openFolds(page);
    await snap(page, "arsenal-real", phone, theme);
  }

  // A test drop, shown with the marker only.
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  const P = runPrefix();
  const stamp = Date.now();
  const ids = [
    await addTestArsenalRef({ category: "film_tv", title: `${P}Never tell anybody outside the family`, room: "negotiation",
                              quote: "Never tell anybody outside the family what you're thinking again." }),
    await addTestArsenalRef({ category: "history_military", title: `${P}Scuttling the ships`, room: "pitch",
                              verification: "corrected", note: "Cortés scuttled his ships; he did not burn them" }),
    await addTestArsenalRef({ category: "sports", title: `${P}And I took that personally`, room: "homies" }),
  ];
  await addTestArsenalDrop(ids, await addTestArsenalWord(`${P}asymmetric ${stamp}`.toLowerCase()));
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/arsenal");
    await expect(page.getByTestId("arsenal-card")).toHaveCount(3);
    await openFolds(page);
    await snap(page, "arsenal-test-drop", phone, theme);
  }
});
