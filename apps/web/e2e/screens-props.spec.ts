import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for the Props tab (owner, Nov 25; docs/props_tab.md) on the REAL board (no marker):
// Props, Safer (the default); Mid sorted by payout; Lines; a two-leg slip (phone: the slip opened). Both themes, phone 390 and desktop 1440. Written to e2e/screenshots/props/ (gitignored).
//   pnpm test:e2e e2e/screens-props.spec.ts --project=phone --workers=1 --output=test-results/screens-props-phone
//   pnpm test:e2e e2e/screens-props.spec.ts --project=desktop --workers=1 --output=test-results/screens-props-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/props";

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = false) {
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${DIR}/${name}-${phone ? "390" : "1440"}-${theme}.png`, fullPage });
}

const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("props screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/betting/props");
  await page.evaluate(() => localStorage.removeItem("vm-combo-legs"));
  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.goto("/betting/props");
    await expect(page.getByTestId("props-page")).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    // The x / % switch sits wholly on screen beside four tabs (it was clipped at 390 on the first run).
    const unit = await page.getByTestId("price-unit").boundingBox();
    expect(unit!.x + unit!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    // The first row sits wholly on the first screen, above the dock and the slip bar (~140 px on the phone).
    const first = await page.locator('[data-testid="single"]').first().boundingBox();
    expect(first!.y + first!.height).toBeLessThan(page.viewportSize()!.height - (phone ? 140 : 0));
    await snap(page, "props-safer", phone, theme);
    await page.goto("/betting/props?tier=mid&sort=mult");
    await expect(page.getByTestId("props-tier-mid")).toHaveAttribute("aria-pressed", "true");
    expect(await noSideScroll(page)).toBe(true);
    await snap(page, "props-mid-payout", phone, theme);
    await page.goto("/betting/props?kind=line");
    await expect(page.getByTestId("props-kind-line")).toHaveAttribute("aria-pressed", "true");
    await snap(page, "lines-safer", phone, theme);
    // A slip of the first two rows, once (it persists across both themes).
    if (theme === "night") {
      await page.goto("/betting/props");
      const rows = page.locator('[data-testid="single"]');
      await rows.nth(0).getByTestId("single-add").click();
      await rows.nth(1).getByTestId("single-add").click();
    }
    await page.goto("/betting/props");
    if (phone) await page.getByTestId("combo-sheet-toggle").click();
    await snap(page, "slip", phone, theme);
  }
  await page.evaluate(() => localStorage.removeItem("vm-combo-legs"));
});
