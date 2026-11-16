import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 8 Phase 3.5, Workstream B, Step 1: the new
// /betting on the REAL board (no test marker), both themes, phone 390x844
// and desktop 1440. The Board; the busiest game's page on each tab (Game
// lines, Players, Halves and quarters); the slip with two legs (phone: the
// docked bar and the open sheet; desktop: the panel beside the board); Today
// and Record. Written to e2e/screenshots/chat8-p35/ (gitignored). The x / %
// choice is set to x and the slip emptied first, and both are emptied after.
//   pnpm test:e2e e2e/screens-betting.spec.ts --project=phone --workers=1 --output=test-results/screens-betting-phone
//   pnpm test:e2e e2e/screens-betting.spec.ts --project=desktop --workers=1 --output=test-results/screens-betting-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat8-p35";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("betting screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/betting");
  await page.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.setItem("vm-price-unit", "x"); });

  // The busiest game of the busiest sport: the most markets on its card.
  await page.goto("/betting");
  const cards = page.getByTestId("board-game");
  await expect(cards.first()).toBeVisible();
  const games = await cards.evaluateAll((els) => els.map((el) => ({
    g: el.getAttribute("data-game") ?? "",
    n: Number(/(\d+) markets/.exec(el.textContent ?? "")?.[1] ?? 0),
  })));
  const busiest = games.sort((a, b) => b.n - a.n)[0].g;

  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.evaluate(() => localStorage.removeItem("vm-combo-legs"));

    await page.goto("/betting");
    await expect(page.getByTestId("betting-tab-board")).toHaveAttribute("aria-current", "page");
    expect(await noSideScroll(page)).toBe(true);
    await snap(page, "board", phone, theme);

    await page.goto(`/betting/game/${busiest}`);
    await expect(page.getByTestId("game-page")).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await snap(page, "game-lines", phone, theme);
    if (phone) await snap(page, "game-lines-full", phone, theme, true);

    await page.getByTestId("game-tab-players").click();
    await expect(page.getByTestId("tab-panel")).toHaveAttribute("data-tab", "players");
    await expect(page.getByTestId("tab-panel").locator('[data-testid="board-market"], p').first()).toBeVisible({ timeout: 15_000 });
    expect(await noSideScroll(page)).toBe(true);
    await snap(page, "game-players", phone, theme);

    await page.getByTestId("game-tab-periods").click();
    await expect(page.getByTestId("tab-panel")).toHaveAttribute("data-tab", "periods");
    await expect(page.getByTestId("tab-panel").locator('[data-testid="board-event"], p').first()).toBeVisible({ timeout: 15_000 });
    await snap(page, "game-periods", phone, theme);

    // Two legs in the slip: the first moneyline, and the first player rung.
    const firstMl = page.getByTestId("ml-pick").first();
    if (await firstMl.count()) await firstMl.click();
    await page.getByTestId("game-tab-players").click();
    const firstRung = page.getByTestId("tab-panel").locator('[data-testid="board-market"][data-payout="binary"]').first();
    if (await firstRung.count()) await firstRung.click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await snap(page, "slip", phone, theme);
    if (phone && await page.getByTestId("combo-sheet").isVisible()) {
      await page.getByTestId("combo-sheet-toggle").click();
      await snap(page, "slip-open", phone, theme);
      await page.getByTestId("combo-sheet-toggle").click();
    }

    await page.goto("/betting/today");
    await snap(page, "today", phone, theme);
    await page.goto("/betting/record");
    await snap(page, "record", phone, theme);
  }
  await page.evaluate(() => { localStorage.removeItem("vm-combo-legs"); localStorage.removeItem("vm-price-unit"); });
});
