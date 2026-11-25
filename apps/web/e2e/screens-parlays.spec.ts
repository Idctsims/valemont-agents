import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 8 Phase 4.5 (docs/parlays_page.md): the Parlays tab on the REAL tickets (no
// marker): today, and the last day with settled tickets; a ticket's Details (phone: the sheet; desktop:
// the panel); the Record. Both themes, phone 390 and desktop 1440. Written to e2e/screenshots/chat8-p45/
// (gitignored).
//   pnpm test:e2e e2e/screens-parlays.spec.ts --project=phone --workers=1 --output=test-results/screens-parlays-phone
//   pnpm test:e2e e2e/screens-parlays.spec.ts --project=desktop --workers=1 --output=test-results/screens-parlays-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat8-p45";

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = false) {
  await page.waitForTimeout(1500);   // the live count and the photos arrive after the first paint
  await page.screenshot({ path: `${DIR}/${name}-${phone ? "390" : "1440"}-${theme}.png`, fullPage });
}

const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("parlays screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/betting/parlays");
  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.goto("/betting/parlays");
    await expect(page.getByTestId("parlays-page")).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await snap(page, "today", phone, theme);
    // The last day with settled tickets: back one day at a time (at most 3).
    for (let i = 0; i < 3 && !(await page.getByTestId("parlays-done").count()); i++) {
      if (!(await page.getByTestId("parlays-prev").count())) break;
      await page.getByTestId("parlays-prev").click();
      await page.waitForLoadState("networkidle");
    }
    await snap(page, "settled", phone, theme);
    const open = page.getByTestId("parlay-details-open").first();
    if (await open.count()) {
      await open.click();
      await expect(page.getByTestId("parlay-details").first()).toBeVisible();
      await snap(page, "details", phone, theme);
      if (phone) await page.keyboard.press("Escape");
    }
    await page.goto("/betting/record");
    await expect(page.getByTestId("record-page")).toBeVisible();
    await snap(page, "record", phone, theme, true);
  }
});
