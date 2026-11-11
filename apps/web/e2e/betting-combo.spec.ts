import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// Combo mode on the Board tab (Chat 8 Phase 3; docs/correlation.md). Needs
// db/076 and db/077 pasted AND the worker with the price_combo job deployed:
// the real worker answers these TEST requests (their legs are test board rows,
// db/076 add_test_kalshi_board with team, so the requests are is_test).
//
// Signed in as the owner's REAL account, on TEST rows only, one test game 3 h out:
//   * the Combo toggle shows YES / NO ticks on binary markets only (the scalar
//     ladder gets none);
//   * two ticks fill the sheet; the ticks survive a reload;
//   * "Price it" shows "Pricing… N s", then the quote: naive, fair, the
//     ratio, the Kalshi naive cost, "Build manually" (the props event is not
//     combo-eligible), each leg's tier, and "unmodeled pair" (the test series
//     has no league, so no rho row: fair = naive);
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
    await signIn(page, "/betting?sport=Basketball");
    await page.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.removeItem("vm-combo-mode"); });
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await page?.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.removeItem("vm-combo-mode"); });
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  const card = () => page.locator(`[data-testid="board-game"][data-game="${game}"]`);
  const market = (text: string) => card().getByTestId("board-market").filter({ hasText: text });
  const sheet = () => page.getByTestId("combo-sheet");

  test("Combo mode ticks binary markets only", async () => {
    await page.goto("/betting?sport=Basketball");
    await page.getByTestId("combo-toggle").click();
    await expect(page.getByTestId("combo-toggle")).toHaveAttribute("aria-pressed", "true");
    await card().getByRole("button").first().click();
    await expect(card().getByTestId("board-market")).toHaveCount(6);
    await expect(card().getByTestId("combo-ticks")).toHaveCount(5);
    await expect(card().locator('[data-payout="scalar"]').getByTestId("combo-ticks")).toHaveCount(0);
  });

  test("two ticks fill the sheet and survive a reload", async () => {
    await market("Test Home").getByTestId("tick-yes").click();
    await market("Test Player: 20+").getByTestId("tick-yes").click();
    await expect(sheet()).toContainText("Combo · 2 legs");
    await expect(market("Test Home").getByTestId("tick-yes")).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(sheet()).toContainText("Combo · 2 legs");
    await expect(sheet().getByTestId("combo-leg")).toHaveCount(2);
  });

  test("Price it: pricing, then the quote", async () => {
    await sheet().getByTestId("combo-price").click();
    await expect(sheet().getByTestId("combo-pricing")).toContainText(/Pricing… \d+ s/);
    const quote = sheet().getByTestId("combo-quote");
    await expect(quote).toBeVisible({ timeout: 30_000 });
    await expect(quote.getByTestId("combo-naive")).toHaveText(/%$/);
    await expect(quote.getByTestId("combo-fair")).toHaveText(/%/);
    await expect(quote.getByTestId("combo-ratio")).toHaveText("×1.00");
    await expect(quote.getByTestId("combo-cost")).toHaveText(/^\$0\.\d\d$/);
    await expect(quote.getByTestId("combo-status")).toContainText("Build manually: not combo-eligible");
    await expect(quote.getByTestId("combo-unmodeled")).toBeVisible();
    await expect(quote.getByTestId("combo-leg-tier")).toHaveCount(2);
  });

  test("both teams' moneylines are refused with the reason", async () => {
    await sheet().getByTestId("combo-clear").click();
    await expect(sheet()).toHaveCount(0);
    await market("Test Home").getByTestId("tick-yes").click();
    await market("Test Away").getByTestId("tick-yes").click();
    await sheet().getByTestId("combo-price").click();
    await expect(sheet().getByTestId("combo-refused")).toContainText("two outcomes of one result", { timeout: 30_000 });
    await sheet().getByTestId("combo-clear").click();
  });
});
