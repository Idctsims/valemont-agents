import { expect, test, type Page } from "@playwright/test";

import { addTestMoveProposal, decideTestMove, hasOwner, setTheme, signIn, useE2eMarker } from "./support/owner";

// Screenshot review for Chat 3 Phase 3: /sports (All), /sports?sport=golf, a
// proposed move on /settings/feed, /news (All), /news?category=deals and
// Home's short feed, in both themes. Phone 390x844 and desktop 1440. Written
// to e2e/screenshots/chat3-p3/ (gitignored). Needs db/042 and db/043.
//   pnpm test:e2e e2e/screens-sports.spec.ts --workers=1 --output=test-results/screens-sports
// One page group at a time (each test is its own sign-in):
//   --grep "sports screens" | "settings feed screens" | "news screens"
//
// The feeds are the REAL feed (no test marker). The proposal is a TEST move
// (add_test_move_proposal: test entities only), which /settings/feed shows
// only to a request with the e2e marker (owner, 2026-10-13), so that one shot
// turns the marker on; the move is dismissed after its shots so none is left
// open on the owner's page.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat3-p3";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

async function start(page: Page, phone: boolean, path: string) {
  test.setTimeout(240_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, path);
}

test("sports screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  await start(page, phone, "/sports");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/sports");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sports News");
    await expect(page.getByTestId("sport-chip-all")).toHaveAttribute("aria-current", "page");
    await snap(page, "sports-all", phone, theme);

    await page.goto("/sports?sport=golf");
    await expect(page.getByTestId("sport-chip-golf")).toHaveAttribute("aria-current", "page");
    await snap(page, "sports-golf", phone, theme);
  }
});

test("settings feed screens", async ({ page, context }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  await start(page, phone, "/settings/feed");
  await useE2eMarker(context, testInfo.project.use.baseURL!);
  const pid = await addTestMoveProposal(`shot-${phone ? "p" : "d"}-${Date.now().toString(36)}`);
  try {
    for (const theme of THEMES) {
      await setTheme(page, theme);
      await page.goto("/settings/feed");
      const proposals = page.getByTestId("proposals");
      await expect(proposals.locator(`[data-proposal-id="${pid}"]`)).toBeVisible();
      await proposals.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -24));
      await snap(page, "settings-feed-proposal", phone, theme, false);
    }
  } finally {
    await decideTestMove(pid, "dismissed");
  }
});

test("news screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  await start(page, phone, "/news");
  for (const theme of THEMES) {
    await setTheme(page, theme);
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
});
