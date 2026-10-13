import { expect, test, type Page } from "@playwright/test";

import { addTestMoveProposal, decideTestMove, hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot review for Chat 3 Phase 3: /sports (All), /sports?sport=golf, a
// proposed move on /settings/feed, /news (All), /news?category=deals and
// Home's short feed, in both themes, on the REAL feed (no test marker). Phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat3-p3/ (gitignored). Needs db/042.
//   pnpm test:e2e e2e/screens-sports.spec.ts --workers=1 --output=test-results/screens-sports
//
// The proposal is a TEST move (add_test_move_proposal: test entities only),
// dismissed after its shots so none is left open on the owner's page.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat3-p3";

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("sports screens", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/sports");
  const pid = await addTestMoveProposal(`shot-${phone ? "p" : "d"}-${Date.now().toString(36)}`);

  try {
    for (const theme of ["night", "day"] as const) {
      await setTheme(page, theme);
      await page.goto("/sports");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sports News");
      await expect(page.getByTestId("sport-chip-all")).toHaveAttribute("aria-current", "page");
      await snap(page, "sports-all", phone, theme);

      await page.goto("/sports?sport=golf");
      await expect(page.getByTestId("sport-chip-golf")).toHaveAttribute("aria-current", "page");
      await snap(page, "sports-golf", phone, theme);

      await page.goto("/settings/feed");
      const proposals = page.getByTestId("proposals");
      await expect(proposals.locator(`[data-proposal-id="${pid}"]`)).toBeVisible();
      await proposals.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -24));
      await snap(page, "settings-feed-proposal", phone, theme, false);

      await page.goto("/news");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("News");
      await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
      await snap(page, "news-all", phone, theme);

      await page.goto("/news?category=deals");
      await expect(page.getByTestId("category-chip-deals")).toHaveAttribute("aria-current", "page");
      await snap(page, "news-deals", phone, theme);

      await page.goto("/");
      await page.getByTestId("home-feed").scrollIntoViewIfNeeded();
      await snap(page, "home-short-feed", phone, theme);
    }
  } finally {
    await decideTestMove(pid, "dismissed");
  }
});
