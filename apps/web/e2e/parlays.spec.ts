import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { hasOwner, signIn, useE2eMarker } from "./support/owner";

// The Parlays tab and the Record (Chat 8 Phase 4.5; docs/parlays_page.md).
// Needs db/091 and the e2e fixtures, written once by the worker:
//   ..\venv\Scripts\python.exe -m scripts.seed_e2e_parlays     (from workers/)
// a Safer ticket that hit (CLV captured, ages 1 / 3 min), a Mid that missed (its close missed), an open
// cross-sport Lotto (extrapolated fill), an empty slot, a no-market sport and a refusal at commit.
// The page reads them with the marker, at ?day=e2e (the fixtures' own day).
//
// Logged out: /betting/parlays asks for sign-in (betting.spec.ts).
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/parlays.spec.ts --project=phone --output=test-results/parlays

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  let context: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/betting/parlays?day=e2e");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await page?.evaluate(() => localStorage.removeItem("vm-combo-legs"));
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  // The fixture day also shows that day's real tickets: the fixtures are the ones whose legs name "Test ".
  const ticket = (tier: string) =>
    page.locator(`[data-testid="parlay-ticket"][data-tier="${tier}"]`).filter({ hasText: "Test " }).first();

  test("each ticket shows its multiplier, paper stake and legs in words; no cents", async () => {
    await page.goto("/betting/parlays?day=e2e");
    await expect(page.getByTestId("betting-tab-parlays")).toHaveAttribute("aria-current", "page");
    const safer = ticket("safer");
    await expect(safer.getByTestId("parlay-mult")).toHaveText("×2.42");
    await expect(safer.getByTestId("parlay-stake")).toHaveText("$41.30");
    await expect(safer).toContainText("PAPER");
    await expect(safer).toContainText("Test Player");
    await expect(safer).toContainText("4+ threes");
    await expect(safer).toContainText("Test Away doesn't win");
    await expect(safer).toContainText("Over 210.5");
    await expect(safer).not.toContainText("E2EPARLAY");
    await expect(page.getByTestId("parlays-page")).not.toContainText("¢");
  });

  test("settled tickets show Hit / Missed and each leg's check or cross", async () => {
    const safer = ticket("safer");
    await expect(safer).toHaveAttribute("data-outcome", "hit");
    await expect(safer.getByTestId("parlay-result")).toHaveText("Hit");
    await expect(safer.locator('[data-testid="parlay-leg"][data-state="hit"]')).toHaveCount(3);
    const mid = ticket("mid");
    await expect(mid.getByTestId("parlay-result")).toHaveText("Missed");
    await expect(mid.locator('[data-testid="parlay-leg"]').first()).toHaveAttribute("data-state", "miss");
    await expect(mid).toContainText("−$15.80");
  });

  test("the open cross-sport Lotto is its own, marked extrapolated fill, with more legs folded", async () => {
    const lotto = ticket("lotto");
    await expect(lotto).toContainText("Cross-sport");
    await expect(lotto).toContainText("extrapolated fill");
    await expect(lotto.getByTestId("parlay-ring")).toHaveAttribute("aria-label", "0 of 11 legs cleared");
    await expect(lotto.locator('[data-testid="parlay-leg"]')).toHaveCount(5);
    await lotto.getByTestId("parlay-more").click();
    await expect(lotto.locator('[data-testid="parlay-leg"]')).toHaveCount(11);
  });

  test("the tier filter is a list, never a total", async () => {
    await page.getByTestId("parlays-filter-mid").click();
    await expect(page.locator('[data-testid="parlay-ticket"]:not([data-tier="mid"])')).toHaveCount(0);
    await expect(ticket("mid")).toBeVisible();
    await page.getByTestId("parlays-filter-all").click();
  });

  test("the status bar: Open, Hit, Missed; counts on each segment; the filters ride in the URL", async () => {
    await page.getByTestId("parlays-status-hit").click();
    await expect(page.getByTestId("parlays-status-hit")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-testid="parlay-ticket"]:not([data-outcome="hit"])')).toHaveCount(0);
    await expect(ticket("safer")).toBeVisible();
    await expect(page).toHaveURL(/status=hit/);
    const hits = Number(await page.getByTestId("parlays-status-hit-n").textContent());
    await expect(page.locator('[data-testid="parlay-ticket"]')).toHaveCount(hits);
    await page.getByTestId("parlays-status-miss").click();
    await expect(ticket("mid")).toBeVisible();
    await expect(page.locator('[data-testid="parlay-ticket"][data-outcome="hit"]')).toHaveCount(0);
    await page.getByTestId("parlays-status-open").click();
    await expect(ticket("lotto")).toBeVisible();
    await expect(page.getByTestId("parlays-done")).toHaveCount(0);
    // A reload keeps the choice.
    await page.reload();
    await expect(page.getByTestId("parlays-status-open")).toHaveAttribute("aria-pressed", "true");
    // The reload's live poll (ESPN, on open) runs first: server actions queue, so let it finish
    // before the next test's Details action, which would wait behind it.
    await page.waitForLoadState("networkidle");
    await page.getByTestId("parlays-status-all").click();
    await expect(page).not.toHaveURL(/status=/);
  });

  test("the day menu lists the shown day, selected, with its ticket count", async () => {
    const day = await page.getByTestId("parlays-page").getAttribute("data-day");
    const menu = page.getByTestId("parlays-day-menu");
    await expect(menu).toHaveValue(day!);
    await expect(menu.locator(`option[value="${day}"]`)).toContainText(/\d+ tickets?/);
  });

  test("Details: stake, payout, the model's chance, CLV (proxy) with its ages and not profit, the log verbatim", async () => {
    await ticket("safer").getByTestId("parlay-details-open").click();
    const d = page.getByTestId("parlay-sheet").getByTestId("parlay-details");
    await expect(d).toBeVisible();
    await expect(d).toContainText("$41.30");
    await expect(d).toContainText("Pays if all hit");
    await expect(d.getByTestId("details-fair")).toHaveText("39.0%");
    await expect(d.getByTestId("details-clv")).toContainText("+1.8%");
    await expect(d.getByTestId("details-ages")).toHaveText("median 1 min · max 3 min");
    await expect(d).toContainText("not profit");
    await expect(d).toContainText("Cohort 2 · P2.1");
    await d.getByTestId("parlay-log").locator("summary").click();
    await expect(d.getByTestId("parlay-log")).toContainText("YES Test Player: 4+ threes (Test Away at Test Home): ask 80¢");
    await page.keyboard.press("Escape");
  });

  test("empty slots open with their binding rules", async () => {
    await page.getByTestId("parlay-empties-open").click();
    const box = page.getByTestId("parlay-empties");
    await expect(box).toContainText("unmodeled pair");
    await expect(box).toContainText("refused at commit");
    await expect(box).toContainText("no eligible market");
    await page.keyboard.press("Escape");
  });

  test("the Record: four agents, cohorts apart, per $1 staked, CLV not profit", async () => {
    await page.goto("/betting/record?cohort=P2.1");
    await expect(page.getByTestId("record-page")).toHaveAttribute("data-cohort", "P2.1");
    await expect(page.getByTestId("record-agent")).toHaveCount(4);
    const safer = page.locator('[data-testid="record-agent"][data-tier="safer"]');
    await expect(safer).toContainText("PAPER");
    await expect(safer).toContainText("per $1 staked");
    await expect(safer).toContainText("not profit");
    await expect(page.locator('[data-testid="record-agent"][data-scope="cross_sport"]')).toContainText("extrapolated fill");
    await expect(page.getByTestId("record-cohort-P2")).toBeVisible();
  });
});
