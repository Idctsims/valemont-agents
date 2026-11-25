import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// /betting (Chat 8 Phase 3.5, Workstream B, Step 1): the Board's cards, a
// game as its own page with three tabs (db/079), the x / % switch, Today and
// Record. Needs db/071-db/083.
//
// Logged out: every /betting view is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/082
// add_test_kalshi_board: series E2ETEST, the marker), one test game 3 h out:
//   * Basketball shows its card as the mock draws it (db/083): the linked
//     test ESPN game's teams, both moneyline prices (Kalshi's ask, away
//     first: AWY 2.56×, HOM 1.59×), the consensus bar in the teams' colours,
//     the spread at the books' line and the total nearest 50c, its claimed
//     edge; the Claimed edges filter keeps it; it opens the game page;
//   * the header: both moneylines, the books' consensus bar (3 books, 64.8%);
//     HOM CLAIMS (a span row, db/082's fixture: +0.17c shipped, +0.10c at the
//     least favourable method), so its price is up and its claim is listed;
//     AWY does not;
//   * tab counts 7 / 4 / 0 (moneyline 2, spread 3, total 2); Players loads its one stat (Points) in place, no
//     page load; "Other" absent (other = 0); the scalar ladder is "Scales";
//     Test Player 10+ passes the shipped method and FAILS the least
//     favourable (a span row): not claimed, no up colour;
//   * the x / % switch flips every price and sticks across a reload;
//   * Refresh shows the source time; the Parlays and Record tabs open (Phase 4.5).
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/betting.spec.ts --project=phone --output=test-results/betting

test.describe("logged out", () => {
  for (const path of ["/betting", "/betting/props", "/betting/today", "/betting/parlays", "/betting/record",
                      "/betting/game/KXNBAGAME-26NOV15GSWHOU"]) {
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
    // The mock's card (db/083): the linked ESPN game's teams, city large, nickname and away / home under it.
    await expect(card().getByTestId("team-name")).toHaveText(["Testville", "Hometown"]);
    await expect(card().getByTestId("matchup-team").first()).toContainText("Away · away");
    await expect(card().getByTestId("matchup-team").last()).toContainText("Home · home");
    // Both moneylines, Kalshi's ask as the big number, away first as the title names them.
    await expect(card().getByTestId("ml-pick")).toHaveCount(2);
    await expect(card().getByTestId("ml-pick").first()).toHaveAttribute("data-ticker", `${game}-AWY`);
    await expect(card().getByTestId("market-price")).toHaveText(["2.56×", "1.59×"]);
    // The books' consensus in the teams' colours: AWY its normalized mid (no outside row), HOM 0.648.
    await expect(card().getByTestId("game-consensus")).toContainText("3 books say");
    await expect(card().getByTestId("bar-away")).toHaveText("38.0%");
    await expect(card().getByTestId("bar-home")).toHaveText("64.8%");
    await expect(card().getByTestId("bar-team").first()).toHaveAttribute("style", /--vm-team:\s*#2b6cb0/);
    // The spread at the books' line (-6.5, not the -3.5 nearer 50c), the total nearest 50c.
    await expect(card().locator('[data-testid="card-line"][data-kind="spread"]')).toContainText("Hometown −6.5");
    await expect(card().locator('[data-testid="card-line"][data-kind="spread"]').getByTestId("card-line-price")).toHaveText("2.50×");
    await expect(card().locator('[data-testid="card-line"][data-kind="total"]')).toContainText("Over 220.5");
    await expect(card().locator('[data-testid="card-line"][data-kind="total"]').getByTestId("card-line-price")).toHaveText("2.08×");
    await expect(card().getByTestId("board-game-open")).toContainText("11 markets");
    await expect(card().getByTestId("card-claimed")).toHaveText("claimed edge");
  });

  test("the Claimed edges filter keeps the claimed test game", async () => {
    await expect(page.getByTestId("filter-all")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("filter-claimed").click();
    await expect(page).toHaveURL(/[?&]claimed=1/);
    await expect(page.getByTestId("filter-claimed")).toHaveAttribute("aria-current", "page");
    await expect(card()).toBeVisible();
    for (const c of await page.getByTestId("board-game").all()) {
      await expect(c.getByTestId("card-claimed")).toBeVisible();
    }
    await page.getByTestId("filter-all").click();
    await expect(page).not.toHaveURL(/claimed=1/);
    await expect(card()).toBeVisible();
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
    // Only a claimed edge is up (db/078). HOM's books row carries a span that claims (db/082's fixture).
    await expect(ml("HOM")).toHaveAttribute("data-claimed", "1");
    await expect(ml("HOM").getByTestId("market-price")).toHaveClass(/text-hit/);
    await expect(ml("AWY")).toHaveAttribute("data-claimed", "0");
    await expect(ml("AWY").getByTestId("market-price")).not.toHaveClass(/text-hit/);
    const claim = page.locator(`[data-testid="box-claim"][data-ticker="${game}-HOM"]`);
    await expect(claim).toHaveAttribute("data-side", "yes");
    await expect(claim).toContainText("Claimed edge +0.2¢ on YES");
    await expect(page.getByTestId("game-tab-lines")).toContainText("7");
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
    // 10+ passes the shipped method (+0.64c) and fails the least favourable (-2.16c): no claim, no up colour.
    const ten = row.locator(`[data-testid="board-market"][data-ticker="${game}PTS-P1-10"]`);
    await expect(ten).toHaveAttribute("data-claimed", "0");
    await expect(ten.getByTestId("market-price")).not.toHaveClass(/text-hit/);
    await expect(panel.getByTestId("box-claim")).toHaveCount(0);
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

  // Phase 4.5: Today became Parlays (its old URL redirects); both tabs open. Their content: e2e/parlays.spec.ts.
  test("Parlays and Record open from the tabs; Today redirects to Parlays", async () => {
    await page.getByTestId("betting-tab-parlays").click();
    await expect(page).toHaveURL(/\/betting\/parlays$/);
    await expect(page.getByTestId("parlays-page")).toBeVisible();
    await page.goto("/betting/today");
    await expect(page).toHaveURL(/\/betting\/parlays$/);
    await expect(page.getByTestId("betting-tab-parlays")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("betting-tab-record").click();
    await expect(page.getByTestId("record-page")).toBeVisible();
    await expect(page.getByTestId("betting-tab-record")).toHaveAttribute("aria-current", "page");
  });
});
