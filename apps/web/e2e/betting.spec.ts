import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// /betting (Chat 8 Phase 3.5, Workstream B, Step 1): the Board's cards, a
// game as its own page with three tabs (db/079), the x / % switch, Today and
// Record. Needs db/071-db/080.
//
// Logged out: every /betting view is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/076
// add_test_kalshi_board: series E2ETEST, the marker), one test game 3 h out:
//   * Basketball shows its card (counts, combo count); it opens the game page;
//   * the header: both moneylines, the books' consensus bar (3 books, 64.8%);
//     NOTHING claims: since db/078 a books row claims only with a four-method
//     span, and the fixture writes none (an untested row), so no up colour;
//   * tab counts 2 / 4 / 0; Players loads its one stat (Points) in place, no
//     page load; "Other" absent (other = 0); the scalar ladder is "Scales";
//   * the x / % switch flips every price and sticks across a reload;
//   * Refresh shows the source time; Today and Record say why they are empty.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/betting.spec.ts --project=phone --output=test-results/betting

test.describe("logged out", () => {
  for (const path of ["/betting", "/betting/today", "/betting/record", "/betting/game/KXNBAGAME-26NOV15GSWHOU"]) {
    test(`${path} asks for sign-in`, async ({ page }) => {
      await page.goto(path);
      await expect(page).toHaveURL(/\/login\?next=%2Fbetting/);
    });
  }
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
    await page.evaluate(() => { localStorage.removeItem("vm-price-unit"); localStorage.removeItem("vm-combo-legs"); });
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await page?.evaluate(() => { localStorage.removeItem("vm-price-unit"); localStorage.removeItem("vm-combo-legs"); });
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  const card = () => page.locator(`[data-testid="board-game"][data-game="${game}"]`);
  const ml = (team: string) => page.locator(`[data-testid="ml-pick"][data-ticker="${game}-${team}"]`);

  test("the Basketball board shows the test game's card", async () => {
    await page.goto("/betting?sport=Basketball");
    await expect(page.getByTestId("betting-tab-board")).toHaveAttribute("aria-current", "page");
    await expect(page.locator('[data-testid="board-sport-chip"][data-sport="Basketball"]')).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("betting-credits")).toContainText("/ 500 used this month");
    await expect(card().getByTestId("game-counts")).toHaveText("2 lines · 3 props · 1 scalar");
    await expect(card()).toContainText("2 combo");
    await expect(card().getByTestId("board-game-open")).toContainText("6 markets");
  });

  test("a game opens as its own page: header, moneylines, consensus, tab counts", async () => {
    const t0 = Date.now();
    await card().getByTestId("board-game-open").click();
    await expect(page).toHaveURL(new RegExp(`/betting/game/${game}$`));
    await expect(page.getByTestId("game-page")).toHaveAttribute("data-game", game);
    await expect(page.getByTestId("ml-pick")).toHaveCount(2);
    console.log(`game page with its lines tab: ${Date.now() - t0} ms`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Test Away vs Test Home");
    await expect(ml("HOM").getByTestId("market-price")).toHaveText("1.59×");
    await expect(ml("AWY").getByTestId("market-price")).toHaveText("2.56×");
    await expect(page.getByTestId("game-consensus")).toContainText("3 books say");
    await expect(page.getByTestId("game-consensus")).toContainText("64.8%");
    // Only a claimed edge is up (db/078). HOM's books row has no span: untested, so not claimed.
    for (const t of ["HOM", "AWY"]) {
      await expect(ml(t)).toHaveAttribute("data-claimed", "0");
      await expect(ml(t).getByTestId("market-price")).not.toHaveClass(/text-hit/);
    }
    await expect(page.getByTestId("box-claims")).toHaveCount(0);
    await expect(page.getByTestId("game-tab-lines")).toContainText("2");
    await expect(page.getByTestId("game-tab-players")).toContainText("4");
    await expect(page.getByTestId("game-tab-periods")).toContainText("0");
    await expect(page.getByTestId("game-tab-lines")).toHaveAttribute("aria-pressed", "true");
  });

  test("Players loads its stat in place, no page load; Other only when there is one", async () => {
    await page.evaluate(() => { (window as unknown as { __stay: number }).__stay = 1; });
    const t0 = Date.now();
    await page.getByTestId("game-tab-players").click();
    const panel = page.getByTestId("tab-panel");
    await expect(panel).toHaveAttribute("data-tab", "players");
    const row = panel.getByTestId("player-row").filter({ hasText: "Test Player" });
    await expect(row.getByTestId("board-market")).toHaveCount(4);
    console.log(`players tab, points: ${Date.now() - t0} ms`);
    await expect(page.getByTestId("stat-chip")).toHaveCount(1);
    await expect(page.locator('[data-testid="stat-chip"][data-value="points"]')).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-testid="stat-chip"][data-value=""]')).toHaveCount(0);
    await expect(row.locator('[data-payout="binary"]')).toHaveCount(3);
    await expect(row.getByTestId("scales-chip")).toHaveText("Scales");
    await expect(row.locator(`[data-ticker="${game}PTS-P1-20"]`).getByTestId("market-label")).toHaveText("20+");
    await expect(row.locator(`[data-ticker="${game}PTS-P1-10P"]`)).toContainText("protected");
    // Back to Game lines and again: from the cache, still the same page.
    await page.getByTestId("game-tab-lines").click();
    await page.getByTestId("game-tab-players").click();
    await expect(row.getByTestId("board-market")).toHaveCount(4);
    expect(await page.evaluate(() => (window as unknown as { __stay?: number }).__stay)).toBe(1);
  });

  test("the x / % switch flips every price, and the choice sticks", async () => {
    await page.getByTestId("unit-pct").click();
    await expect(page.getByTestId("price-unit")).toHaveAttribute("data-unit", "pct");
    await expect(page.locator(`[data-ticker="${game}PTS-P1-20"]`).getByTestId("market-price")).toHaveText("17%");
    await expect(ml("HOM").getByTestId("market-price")).toHaveText("63%");
    await page.reload();
    await expect(page.getByTestId("unit-pct")).toHaveAttribute("aria-pressed", "true");
    await expect(ml("HOM").getByTestId("market-price")).toHaveText("63%");
    await page.getByTestId("unit-x").click();
    await expect(ml("HOM").getByTestId("market-price")).toHaveText("1.59×");
  });

  test("Refresh shows the source time; an empty tab says so", async () => {
    await page.getByTestId("tab-refresh").click();
    await expect(page.getByTestId("tab-asof")).toContainText(/Prices as of \d{1,2}:\d\d [AP]M · before fee/);
    await page.getByTestId("game-tab-periods").click();
    await expect(page.getByTestId("tab-panel")).toContainText("No half or quarter markets are open for this game yet.");
  });

  test("Today and Record are honest empty states", async () => {
    await page.getByTestId("betting-tab-today").click();
    await expect(page).toHaveURL(/\/betting\/today$/);
    await expect(page.getByTestId("today-empty")).toHaveText("Parlays drop with Phase 4.");
    await page.getByTestId("betting-tab-record").click();
    await expect(page.getByTestId("record-empty")).toHaveText("No committed tickets yet.");
    await expect(page.getByTestId("betting-tab-record")).toHaveAttribute("aria-current", "page");
  });
});
