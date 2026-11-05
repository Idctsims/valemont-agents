import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 7 Phase 3: /gameday (the five), /gameday with
// All sports, and the detail page of tonight's first game (or the week's,
// on a dead night), both themes. Phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat7-p3/ (gitignored). Needs db/068. The REAL slate (no
// test marker).
//   pnpm test:e2e e2e/screens-gameday.spec.ts --project=phone --workers=1 --output=test-results/screens-gameday-phone
//   pnpm test:e2e e2e/screens-gameday.spec.ts --project=desktop --workers=1 --output=test-results/screens-gameday-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat7-p3";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("game day screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(240_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/gameday");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/gameday");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Game Day");
    await expect(page.getByTestId("gameday-chip-five")).toHaveAttribute("aria-current", "page");
    // One-handed on a phone: no sideways scroll.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await snap(page, "gameday-five", phone, theme);
    if (phone) await snap(page, "gameday-five-full", phone, theme, true);

    await page.goto("/gameday?sports=all");
    await expect(page.getByTestId("gameday-chip-all")).toHaveAttribute("aria-current", "page");
    await snap(page, "gameday-all", phone, theme);

    await page.goto("/gameday");
    const first = page.getByTestId("game-row").first();
    if (await first.count()) {
      await first.click();
      await expect(page.getByTestId("game-detail")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await snap(page, "gameday-detail", phone, theme, true);
    }
  }
});
