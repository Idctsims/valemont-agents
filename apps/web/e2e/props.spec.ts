import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestKalshiBoard, hasOwner, signIn, useE2eMarker } from "./support/owner";

// The Props tab (owner, Nov 25; docs/props_tab.md), on db/093's kalshi_singles.
// The fixture is db/083's e2e board (add_test_kalshi_board, Basketball, three
// hours out): Test Player 10+ pts (YES 0.58, a books line), 20+ (YES 0.17), the
// moneylines, spreads and totals. A search for "Test Player" keeps the real
// board out of the way.
//
// Logged out: /betting/props asks for sign-in (betting.spec.ts).
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/props.spec.ts --project=phone --output=test-results/props

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext;
  let page: Page;
  let game = "";
  const base = "/betting/props?sport=Basketball&q=Test+Player";

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    game = await addTestKalshiBoard(new Date(Date.now() + 3 * 3600_000).toISOString());
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, base);
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

  const row = (suffix: string, side: "yes" | "no" = "yes") =>
    page.locator(`[data-testid="single"][data-ticker="${game}PTS${suffix}"][data-side="${side}"]`);

  test("the tab opens on Props, Safer; the tier bar counts each tier", async () => {
    await page.goto(base);
    await expect(page.getByTestId("betting-tab-props")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("props-kind-prop")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("props-tier-safer")).toHaveAttribute("aria-pressed", "true");
    for (const t of ["safer", "mid", "lotto"]) {
      await expect(page.getByTestId(`props-tier-${t}-n`)).toHaveText(/^\d+$/);
    }
    await expect(page.getByTestId("props-page")).not.toContainText("E2ETEST");
  });

  test("a Mid prop in words, its price after fee, its books edge; a Lotto prop", async () => {
    await page.getByTestId("props-tier-mid").click();
    await expect(page).toHaveURL(/tier=mid/);
    const p10 = row("-P1-10");
    await expect(p10).toContainText("Test Player");
    await expect(p10).toContainText("10+ pts");
    await expect(p10.getByTestId("single-source")).toHaveText("books");
    await expect(p10.getByTestId("single-edge")).toContainText("vs books");
    await expect(p10.getByTestId("single-price")).toHaveText(/×$/);
    await page.getByTestId("props-tier-lotto").click();
    await expect(row("-P1-20")).toContainText("20+ pts");
    await expect(row("-P1-20").getByTestId("single-edge")).toContainText("to trade");
  });

  test("+ Slip adds a side; two props from the tab make a two-leg slip; the filters survive a reload", async () => {
    await row("-P1-20").getByTestId("single-add").click();
    await expect(row("-P1-20").getByTestId("single-add")).toHaveText("In slip");
    await page.getByTestId("props-tier-mid").click();
    await row("-P1-10").getByTestId("single-add").click();
    await expect(page.getByTestId("slip-head")).toContainText("2 legs");
    await page.reload();
    await expect(page.getByTestId("props-tier-mid")).toHaveAttribute("aria-pressed", "true");
    await expect(row("-P1-10").getByTestId("single-add")).toHaveText("In slip");
    await row("-P1-10").getByTestId("single-add").click();
    await expect(page.getByTestId("slip-head")).toContainText("1 leg");
  });

  test("Lines, the multiplier sort, and the x / % switch", async () => {
    await page.goto("/betting/props?sport=Basketball&q=Test&tier=safer");
    await page.getByTestId("props-kind-line").click();
    await expect(page).toHaveURL(/kind=line/);
    await expect(page.locator('[data-testid="single"]').first()).toBeVisible();
    await expect(page.locator(`[data-testid="single"][data-ticker^="${game}PTS"]`)).toHaveCount(0);
    await page.getByTestId("props-sort-mult").click();
    await expect(page).toHaveURL(/sort=mult/);
    const prices = await page.getByTestId("single-price").allTextContents();
    const xs = prices.map((t) => Number(t.replace("×", "")));
    expect(xs).toEqual([...xs].sort((a, b) => b - a));
    await page.getByTestId("unit-pct").click();
    await expect(page.getByTestId("single-price").first()).toHaveText(/%$/);
    await page.getByTestId("unit-x").click();
  });
});
