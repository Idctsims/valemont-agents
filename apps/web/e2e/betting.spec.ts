import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// Betting, the Board tab (Chat 8 Phase 1; docs/kalshi_board.md), needs db/071.
//
// Logged out: /betting is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/071
// add_test_kalshi_board: series E2ETEST, the marker), one test game 3 h out:
//   * the Basketball chip shows it, collapsed (counts, start, combo count);
//   * expanding loads its markets: game lines first, then props; YES bid/ask
//     and the fee per contract at the ask; the combo chip (YES only) on the
//     moneyline event; the protected variant marked;
//   * db/072: the ladder event's one scalar market shows "Scales" and its
//     event no combo chip (although combo-eligible); scalar is counted apart;
//   * the kind and combo filters, kept in the URL (combo drops scalar).
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/betting.spec.ts --project=phone --output=test-results/betting

test.describe("logged out", () => {
  test("/betting asks for sign-in", async ({ page }) => {
    await page.goto("/betting");
    await expect(page).toHaveURL(/\/login\?next=%2Fbetting/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext;
  let page: Page;
  let game = "";

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    game = await addTestKalshiBoard(new Date(Date.now() + 3 * 3600_000).toISOString());
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/betting?sport=Basketball");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  const card = () => page.locator(`[data-testid="board-game"][data-game="${game}"]`);

  test("the Basketball chip shows the test game, collapsed", async () => {
    await page.goto("/betting?sport=Basketball");
    await expect(page.getByTestId("betting-tab-board")).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('[data-testid="board-sport-chip"][data-sport="Basketball"]')).toHaveAttribute("aria-current", "page");
    await expect(card()).toContainText("Test Away vs Test Home");
    await expect(card().getByTestId("game-counts")).toHaveText("2 lines · 3 props · 1 scalar");
    await expect(card()).toContainText("2 combo");
    await expect(card().getByTestId("board-game-markets")).toHaveCount(0);
  });

  test("expanding loads the markets: lines first, then props", async () => {
    await card().getByRole("button").click();
    const markets = card().getByTestId("board-market");
    await expect(markets).toHaveCount(6);
    await expect(markets.first()).toHaveAttribute("data-kind", "game_line");
    await expect(markets.nth(2)).toHaveAttribute("data-kind", "prop");
    const home = markets.filter({ hasText: "Test Home" });
    await expect(home.getByTestId("market-yes")).toHaveText("61¢ / 63¢");
    await expect(home.getByTestId("market-fee")).toHaveText("fee 1.63¢");
    const ml = card().getByTestId("board-event").first();
    await expect(ml.getByTestId("combo-chip")).toHaveText("Combo · YES only · max 1");
    await expect(ml.getByTestId("combo-chip")).toHaveAttribute("data-size-max", "1");
    await expect(card().locator('[data-testid="board-event"][data-event$="PTS"]').getByTestId("combo-chip")).toHaveCount(0);
    await expect(markets.filter({ hasText: "protected" })).toHaveCount(1);
    // The scalar ladder: "Scales", and no combo chip on its event (combo-eligible on Kalshi).
    const ladder = card().locator('[data-testid="board-event"][data-event$="LAD"]');
    await expect(ladder.getByTestId("combo-chip")).toHaveCount(0);
    await expect(ladder.getByTestId("board-market")).toHaveAttribute("data-payout", "scalar");
    await expect(ladder.getByTestId("scales-chip")).toHaveText("Scales");
    await expect(card().getByTestId("scales-chip")).toHaveCount(1);
    await expect(ladder).toContainText("Test Player: points, $1 at 50");
    await expect(ml.getByTestId("event-asof")).toContainText("as of");
  });

  test("filters: props only, then combo-eligible only, in the URL", async () => {
    await page.getByTestId("board-kind-prop").click();
    await expect(page).toHaveURL(/kind=props/);
    await expect(card().getByTestId("game-counts")).toHaveText("3 props · 1 scalar");
    await page.getByTestId("board-combo-filter").click();
    await expect(page).toHaveURL(/kind=props.*combo=1|combo=1.*kind=props/);
    // The binary props are not combo-eligible and the combo filter drops the
    // scalar ladder (never a leg): the game drops out.
    await expect(card()).toHaveCount(0);
    await page.getByTestId("board-kind-all").click();
    await expect(page).toHaveURL(/combo=1/);
    await expect(card().getByTestId("game-counts")).toHaveText("2 lines");
  });
});
