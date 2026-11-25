import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// "Bet $X to win $Y" (E1; docs/payout_target.md), on db/094 and the worker's
// build_payout job: it needs the deployed worker. A marked request searches the
// test board only (db/083's add_test_kalshi_board: one game, every pair same-game
// and unpriced, so only single bets can be built): $10 to get $20 back is the
// total at 48c (TOT-220 YES). Nothing bets.
//
// Run (owner, CLAUDE.md §6, the Read-Host form), after the merge deploys:
//   pnpm test:e2e e2e/payout.spec.ts --project=phone --output=test-results/payout

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await addTestKalshiBoard(new Date(Date.now() + 3 * 3600_000).toISOString());
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/betting/props");
    await page.evaluate(() => localStorage.removeItem("vm-combo-legs"));
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await page?.evaluate(() => localStorage.removeItem("vm-combo-legs"));
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("the sheet: amounts, the multiple, the warning past 100x, a refused amount", async () => {
    await page.goto("/betting/props");
    await page.getByTestId("payout-open").click();
    await expect(page.getByTestId("payout-sheet")).toBeVisible();
    await page.getByTestId("payout-stake").fill("10");
    await page.getByTestId("payout-back").fill("2000");
    await expect(page.getByTestId("payout-multiple")).toHaveText("200× your stake");
    await expect(page.getByTestId("payout-warn")).toBeVisible();
    await page.getByTestId("payout-back").fill("5");
    await expect(page.getByTestId("payout-multiple")).toHaveText("Pays back has to be more than the stake.");
    await expect(page.getByTestId("payout-build")).toBeDisabled();
  });

  test("$10 to $20: the worker answers; the ticket pays at least $20, worded, with its chance; it loads into the slip", async () => {
    test.setTimeout(120_000);
    await page.getByTestId("payout-back").fill("20");
    await expect(page.getByTestId("payout-warn")).toHaveCount(0);
    await page.getByTestId("payout-build").click();
    const answer = page.getByTestId("payout-answer");
    await expect(answer).toBeVisible({ timeout: 90_000 });
    await expect(answer).toHaveAttribute("data-nearest", "0");
    const first = page.getByTestId("payout-ticket").first();
    const pays = Number((await first.getByTestId("payout-pays").textContent())!.replace(/[$,]/g, ""));
    expect(pays).toBeGreaterThanOrEqual(20);
    expect(pays).toBeLessThanOrEqual(22);
    await expect(first.getByTestId("payout-chance")).toHaveText(/^about \d+ in \d+$/);
    await expect(first).not.toContainText("E2ETEST");
    await first.getByTestId("payout-load").click();
    await expect(page.getByTestId("payout-sheet")).not.toBeVisible();
    await expect(page.getByTestId("slip-head")).toContainText("1 leg");
  });
});
