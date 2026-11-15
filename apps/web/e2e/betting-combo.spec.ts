import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// The slip on a game's page (Chat 8 Phase 3.5, Workstream B; combo pricing
// unchanged underneath: db/076, db/077). Needs the worker with the
// price_combo job deployed: the real worker answers these TEST requests (their
// legs are test board rows, so the requests are is_test).
//
// Signed in as the owner's REAL account, on TEST rows only, one test game 3 h out:
//   * tapping a price fills the slip (no Combo mode any more): HOM to win
//     (0.63) and Test Player 20+ (0.17); the bar docks above the tab bar;
//   * the total is AFTER fee, one taker fee on the combo's price:
//     0.63 × 0.17 = 0.1071, fee 0.006695 (rounded up), so 8.79× or 11.4%;
//     each leg's price is before fee, and says so;
//   * the legs survive a reload;
//   * "Price it" shows "Pricing… N s", then the quote: naive, fair, the
//     ratio, Phase 3's per-leg-fee cost, "Build manually" (the props event is
//     not combo-eligible), each leg's tier and age, "unmodeled pair";
//   * both teams' moneylines: refused, "two outcomes of one result".
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/betting-combo.spec.ts --project=phone --output=test-results/betting-combo

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
    await signIn(page, `/betting/game/${game}`);
    await page.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.removeItem("vm-price-unit"); });
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await page?.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.removeItem("vm-price-unit"); });
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  const slip = () => page.getByTestId("combo-sheet");
  const ml = (team: string) => page.locator(`[data-testid="ml-pick"][data-ticker="${game}-${team}"]`);
  const rung = (suffix: string) => page.locator(`[data-testid="board-market"][data-ticker="${game}${suffix}"]`);
  // Tap a page element after scrolling it just under the sticky header, as a
  // person would: Playwright's own scroll can leave it under the docked slip
  // bar or the tab bar, which then takes the tap (owner rerun, Nov 12).
  const tap = async (target: Locator) => {
    await target.evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 160));
    await target.click();
  };
  const openSlip = async () => {
    const t = page.getByTestId("combo-sheet-toggle");
    if ((await t.getAttribute("aria-expanded")) !== "true") await t.click();
    await expect(t).toHaveAttribute("aria-expanded", "true");
  };
  const closeSlip = async () => {
    const t = page.getByTestId("combo-sheet-toggle");
    if ((await t.getAttribute("aria-expanded")) === "true") await t.click();
  };

  test("tapping prices fills the slip; the total is after fee", async () => {
    await page.goto(`/betting/game/${game}`);
    await expect(slip()).toBeHidden(); // an empty slip shows no bar on the phone
    await tap(ml("HOM"));
    await expect(ml("HOM")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("slip-head")).toHaveText("1 leg");
    await tap(page.getByTestId("game-tab-players"));
    await tap(rung("PTS-P1-20"));
    await expect(rung("PTS-P1-20")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("slip-head")).toHaveText("2 legs · 8.79×");

    await openSlip();
    await expect(slip().getByTestId("combo-leg")).toHaveCount(2);
    await expect(slip()).toContainText("Leg prices before fee");
    await expect(slip().getByTestId("slip-leg-price")).toHaveText(["1.59×", "5.88×"]);
    await expect(slip().getByTestId("slip-total")).toHaveText("8.79×");
    await expect(slip()).toContainText("Pays on $1, after fee");
    await expect(slip().getByTestId("slip-fee")).toHaveText("0.67¢");
    await expect(slip().getByTestId("slip-price")).toHaveText("9.34×");
    await expect(slip().getByTestId("slip-games")).toHaveText("Same game");

    await closeSlip();
    await page.getByTestId("unit-pct").click();
    await openSlip();
    await expect(slip().getByTestId("slip-total")).toHaveText("11.4%");
    await expect(slip()).toContainText("Costs, after fee");
    await expect(slip().getByTestId("slip-leg-price")).toHaveText(["63%", "17%"]);
    await closeSlip();
    await page.getByTestId("unit-x").click();
  });

  test("the legs survive a reload", async () => {
    await page.reload();
    await expect(page.getByTestId("slip-head")).toHaveText("2 legs · 8.79×");
    await expect(ml("HOM")).toHaveAttribute("aria-pressed", "true");
  });

  test("Price it: pricing, then the quote", async () => {
    await openSlip();
    await slip().getByTestId("combo-price").click();
    await expect(slip().getByTestId("combo-pricing")).toContainText(/Pricing… \d+ s/);
    const quote = slip().getByTestId("combo-quote");
    await expect(quote).toBeVisible({ timeout: 30_000 });
    await expect(quote.getByTestId("combo-naive")).toHaveText(/%$/);
    await expect(quote.getByTestId("combo-fair")).toHaveText(/%/);
    await expect(quote.getByTestId("combo-ratio")).toHaveText("×1.00");
    await expect(quote.getByTestId("combo-cost")).toHaveText(/^\$0\.\d\d$/);
    await expect(quote.getByTestId("combo-status")).toContainText("Build manually: not combo-eligible");
    await expect(quote.getByTestId("combo-unmodeled")).toBeVisible();
    await expect(quote.getByTestId("combo-leg-tier")).toHaveCount(2);
    await expect(quote.getByTestId("combo-leg-age")).toHaveCount(2);
    await expect(quote.getByTestId("combo-leg-age").first()).toHaveText(/^price .*old$/);
    await expect(quote.getByTestId("combo-prices-stale")).toHaveCount(0);
    // The total stays after fee beside the quote.
    await expect(slip().getByTestId("slip-total")).toHaveText("8.79×");
  });

  test("both teams' moneylines are refused with the reason", async () => {
    // The worker answers through the queue, and the refusal wait alone is 30 s.
    test.setTimeout(60_000);
    await slip().getByTestId("combo-clear").click();
    await expect(slip()).toBeHidden();
    expect(await page.evaluate(() => localStorage.getItem("vm-combo-legs"))).toBeNull();
    await expect(ml("HOM")).toHaveAttribute("aria-pressed", "false");

    await tap(ml("HOM"));
    await tap(ml("AWY"));
    await expect(page.getByTestId("slip-head")).toHaveText("2 legs · 3.87×");
    await openSlip();
    await slip().getByTestId("combo-price").click();
    await expect(slip().getByTestId("combo-refused")).toContainText("two outcomes of one result", { timeout: 30_000 });
    await slip().getByTestId("combo-clear").click();
  });
});
