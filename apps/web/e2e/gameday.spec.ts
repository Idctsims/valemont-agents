import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { gameDay } from "../src/lib/gameday/slate";
import { addTestGamedayEvent, hasOwner, runPrefix, setTestSportsLive, signIn, useE2eMarker } from "./support/owner";

// Game Day (Chat 7 Phase 3; docs/gameday.md), needs db/068.
//
// Logged out: /gameday and a detail page are owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/068
// add_test_gameday_event: is_test, an e2e- id, the marker), all on tonight's
// game day (05:00 to 05:00 Chicago):
//   * Alabama sits at the top of Tonight;
//   * TBD where ESPN has no time; "Not listed" for a blank broadcast;
//   * the gold mark on a game on the owner's services (CBS -> Paramount+);
//   * All sports adds the other leagues, kept in the URL;
//   * a live game's score changes without a reload (the 60 s poll);
//   * the detail page: where to watch, the Kalshi state with its reason,
//     venue and neutral site, and the empty "Your bets" (Chat 9).
// The test rows stay (sports_events has no delete path); each carries this
// run's prefix and shows only with the marker.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/gameday.spec.ts --project=phone --output=test-results/gameday

test.describe("logged out", () => {
  test("/gameday asks for sign-in", async ({ page }) => {
    await page.goto("/gameday");
    await expect(page).toHaveURL(/\/login\?next=%2Fgameday/);
  });

  test("a game page asks for sign-in", async ({ page }) => {
    await page.goto("/gameday/1");
    await expect(page).toHaveURL(/\/login\?next=%2Fgameday%2F1/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const today = gameDay(new Date());
  /** A Chicago wall-clock time on tonight's game day, as Postgres reads it. */
  const tonight = (hhmm: string) => `${today} ${hhmm}:00 America/Chicago`;
  let context: BrowserContext;
  let page: Page;
  const ids: Record<string, number> = {};

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    ids.bama = await addTestGamedayEvent({
      league: "cfb", startsAt: tonight("21:30"), home: `${P}LSU Tigers`, away: `${P}Alabama Crimson Tide`,
      broadcast: ["ABC"], participants: { home: { id: "99", rank: 14, conference_id: "8" }, away: { id: "333", rank: 5, conference_id: "8" } },
      linkState: "not_linked", linkReason: `${P}no Kalshi event for this pair`, venue: `${P}Tiger Stadium`, neutral: true,
    });
    ids.nfl = await addTestGamedayEvent({ league: "nfl", startsAt: tonight("19:15"), home: `${P}NFL Home`, away: `${P}NFL Away`,
                                          broadcast: ["CBS"], linkState: "not_listed", linkReason: "after the latest listed event" });
    ids.tbd = await addTestGamedayEvent({ league: "cfb", startsAt: `${today} 00:00:00 America/New_York`, timeTbd: true,
                                          home: `${P}TBD Home`, away: `${P}TBD Away` });
    ids.nhl = await addTestGamedayEvent({ league: "nhl", startsAt: tonight("20:00"), home: `${P}NHL Home`, away: `${P}NHL Away`,
                                          broadcast: ["TNT"] });
    const started = new Date(Date.now() - 30 * 60_000).toISOString();
    ids.live = await addTestGamedayEvent({ league: "nba", startsAt: started, home: `${P}Live Home`, away: `${P}Live Away`,
                                           status: "live", broadcast: ["ESPN"], score: { home: 0, away: 0 } });
    await setTestSportsLive(ids.live, "live", { home: 0, away: 0 }, "Q1 11:40", "11:40");

    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/gameday");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  const row = (id: number) => page.locator(`[data-testid="game-row"][data-id="${id}"]`);

  test("Alabama sits at the top of Tonight", async () => {
    await page.goto("/gameday");
    const first = page.getByTestId("section-tonight").getByTestId("game-row").first();
    await expect(first).toHaveAttribute("data-bama", "true");
    await expect(row(ids.bama)).toContainText("No. 5");
    await expect(row(ids.bama)).toContainText(`${P}Alabama Crimson Tide`);
    await expect(row(ids.bama).getByTestId("game-start")).toHaveText("9:30 PM");
  });

  test("TBD where ESPN has no time; Not listed for a blank broadcast", async () => {
    await expect(row(ids.tbd).getByTestId("game-start")).toHaveText("TBD");
    await expect(row(ids.tbd).getByTestId("game-tv")).toHaveText("Not listed");
  });

  test("the gold mark on a game the owner can stream, and not on one he can't", async () => {
    await expect(row(ids.nfl).getByTestId("service-mark")).toHaveText("Paramount+");
    await expect(row(ids.bama).getByTestId("service-mark")).toHaveCount(0); // ABC
  });

  test("All sports adds the other leagues, kept in the URL", async () => {
    await expect(row(ids.nhl)).toHaveCount(0);
    await page.getByTestId("gameday-chip-all").click();
    await expect(page).toHaveURL(/\/gameday\?sports=all$/);
    await expect(page.getByTestId("gameday-chip-all")).toHaveAttribute("aria-current", "page");
    await expect(row(ids.nhl)).toBeVisible();
    await expect(row(ids.nhl).getByTestId("service-mark")).toHaveText("HBO Max"); // TNT
    await page.getByTestId("gameday-chip-five").click();
    await expect(page).toHaveURL(/\/gameday$/);
    await expect(row(ids.nhl)).toHaveCount(0);
  });

  test("a live game's score changes without a reload", async () => {
    test.setTimeout(150_000);
    await page.goto("/gameday");
    const live = page.getByTestId("section-live").locator(`[data-testid="game-row"][data-id="${ids.live}"]`);
    await expect(live.getByTestId("score-home")).toHaveText("0");
    await expect(page.getByTestId("gameday-slate")).toHaveAttribute("data-polling", "true");
    const navigations: string[] = [];
    page.on("framenavigated", (f) => { if (f === page.mainFrame()) navigations.push(f.url()); });
    await setTestSportsLive(ids.live, "live", { home: 7, away: 3 }, "Q2 4:12", "4:12");
    await expect(live.getByTestId("score-home")).toHaveText("7", { timeout: 90_000 });
    await expect(live.getByTestId("score-away")).toHaveText("3");
    expect(navigations, "no reload").toEqual([]);
    expect(Number(await page.getByTestId("gameday-slate").getAttribute("data-polls"))).toBeGreaterThan(0);
  });

  test("tapping a game opens its detail: watch, Kalshi with the reason, venue, Your bets", async () => {
    await page.goto("/gameday");
    await row(ids.bama).click();
    await expect(page).toHaveURL(new RegExp(`/gameday/${ids.bama}$`));
    await expect(page.getByTestId("detail-watch")).toContainText("ABC");
    await expect(page.getByTestId("kalshi-state")).toHaveAttribute("data-state", "not_linked");
    await expect(page.getByTestId("kalshi-state")).toContainText("Not linked");
    await expect(page.getByTestId("kalshi-reason")).toHaveText(`${P}no Kalshi event for this pair`);
    await expect(page.getByTestId("detail-venue")).toContainText(`${P}Tiger Stadium`);
    await expect(page.getByTestId("detail-neutral")).toHaveText("Neutral site");
    await expect(page.getByTestId("detail-bets")).toContainText("Chat 9");

    await page.goto(`/gameday/${ids.nfl}`);
    await expect(page.getByTestId("kalshi-state")).toHaveText("Not listed yet");
    await expect(page.getByTestId("detail-watch").getByTestId("service-mark")).toHaveText("Paramount+");
  });

  test("an unknown game is a 404", async () => {
    const res = await page.goto("/gameday/999999999999");
    expect(res?.status()).toBe(404);
  });
});
