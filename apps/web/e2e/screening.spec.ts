import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestTitle, addTestTrailer, hasOwner, runPrefix, signIn, storyReactions, useE2eMarker, watchHistory } from "./support/owner";

// The Screening Room (Chat 4 Phase 3; docs/screening.md), needs db/051 and
// TMDB_READ_TOKEN in apps/web/.env.local (the search and title pages call
// TMDB from the local server).
//
// Logged out: /screening and a title page are owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/051
// add_test_title / add_test_trailer: is_test, the e2e marker):
//   * search returns results (live TMDB);
//   * a test title moves Want -> Watching -> Finished; finished_at is set (by
//     the database) and every move is a row;
//   * a real title's page shows "Tonight on your services" before rent/buy;
//   * a trailer reaction writes pillar screening only;
//   * a test title is invisible without the marker.
// The test rows stay (append-only); each is named with this run's prefix.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/screening.spec.ts --project=phone --output=test-results/screening

/** The City of God (movie 598): streaming on Paramount+ (Essential) with rent
 * and buy too, on 2026-10-14. A real title, read only. */
const CITY_OF_GOD = "/screening/title/movie/598";

test.describe("logged out", () => {
  test("/screening asks for sign-in", async ({ page }) => {
    await page.goto("/screening");
    await expect(page).toHaveURL(/\/login\?next=%2Fscreening/);
  });

  test("a title page asks for sign-in", async ({ page }) => {
    await page.goto(CITY_OF_GOD);
    await expect(page).toHaveURL(/\/login\?next=%2Fscreening%2Ftitle/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  let context: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/screening");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("search returns results, with poster, year and kind", async () => {
    await page.goto("/screening");
    await expect(page.getByTestId("tmdb-attribution")).toContainText("not endorsed or certified by TMDB");
    await expect(page.getByTestId("tmdb-logo")).toHaveAttribute("src", "/tmdb-logo.svg");
    expect(await page.getByTestId("tmdb-logo").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    const started = Date.now();
    await page.getByTestId("screening-search-input").fill("Succession");
    await expect(page.getByTestId("screening-search-state")).toHaveAttribute("data-state", "done");
    const took = Date.now() - started;
    const results = page.getByTestId("screening-search-result");
    await expect(results.first()).toBeVisible();
    await expect(results.filter({ hasText: "Succession" }).first()).toContainText(/Series · 2018/);
    // The done-when: "under a second" includes the 250 ms debounce. Reported, not asserted hard.
    test.info().annotations.push({ type: "search_ms", description: String(took) });
  });

  test("Want -> Watching -> Finished, and Finished records the date", async () => {
    const name = `${P}shelf ${Date.now()}`;
    const t = await addTestTitle({ kind: "tv", name });
    // Shelve it from its (database-only) title page: a test title is not on TMDB.
    await page.goto(`/screening/title/tv/${t.tmdbId}`);
    await expect(page.getByTestId("title-name")).toHaveText(name);
    await page.getByTestId("shelf-want").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    await page.goto("/screening");
    const row = () => page.getByTestId("shelf-title").filter({ hasText: name });
    await expect(page.getByTestId("shelf-section-want").getByTestId("shelf-title").filter({ hasText: name })).toHaveCount(1);
    // It comes out in 10 days: Upcoming lists it.
    await expect(page.getByTestId("upcoming").getByText(name)).toBeVisible();
    await row().getByTestId("shelf-watching").click();
    await expect(page.getByTestId("shelf-section-watching").getByTestId("shelf-title").filter({ hasText: name })).toHaveCount(1);
    await row().getByTestId("shelf-finished").click();
    await expect(page.getByTestId("shelf-section-finished").getByTestId("shelf-title").filter({ hasText: name })).toHaveCount(1);
    await expect(row()).toContainText(/finished [A-Z][a-z]{2} \d{1,2}/);
    await expect(page.getByTestId("upcoming").getByText(name)).toHaveCount(0);
    await expect.poll(async () => (await watchHistory(t.id)).map((h) => [h.status, h.finished_at !== null]))
      .toEqual([["want", false], ["watching", false], ["finished", true]]);
  });

  test("a title page shows Tonight on your services before rent and buy", async () => {
    await page.goto(CITY_OF_GOD);
    await expect(page.getByTestId("title-name")).toHaveText("City of God");
    const tonight = page.getByTestId("tonight");
    const rentBuy = page.getByTestId("rent-buy");
    await expect(tonight.getByTestId("tonight-service")).toContainText(["Paramount+"]);
    await expect(rentBuy).toBeVisible();
    const [a, b] = await Promise.all([tonight.boundingBox(), rentBuy.boundingBox()]);
    expect(a!.y).toBeLessThan(b!.y);
    await expect(page.getByTestId("justwatch-attribution")).toBeVisible();
    await expect(page.getByTestId("title-trailer").locator("iframe")).toHaveAttribute("src", /^https:\/\/www\.youtube-nocookie\.com\/embed\//);
  });

  test("a trailer reaction writes pillar screening only", async () => {
    const stamp = Date.now();
    const show = await addTestTitle({ kind: "movie", name: `${P}film ${stamp}` });
    const title = `${P}trailer ${stamp} | Official Trailer`;
    const id = await addTestTrailer({ title, url: `https://trailers.e2e.invalid/${stamp}`, titleId: show.id });
    const unmatched = `${P}unmatched ${stamp} | Official Teaser`;
    await addTestTrailer({ title: unmatched, url: `https://trailers.e2e.invalid/u-${stamp}`, titleId: null });
    await page.goto("/screening?tab=trailers");
    const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: title });
    await expect(card.getByTestId("trailer-title")).toContainText(`${P}film ${stamp}`);
    await expect(page.getByTestId("feed-card").filter({ hasText: unmatched }).getByTestId("trailer-unmatched")).toBeVisible();
    await card.getByTestId("react-love").click();
    await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => storyReactions(id)).toEqual([{ pillar: "screening", reaction: "love" }]);
    // No trailer on the other feeds.
    for (const url of ["/", "/news?feed=latest", "/sports?feed=latest", "/tech?feed=latest"]) {
      await page.goto(url);
      await expect(page.getByText(title), url).toHaveCount(0);
    }
  });

  test("a test title is invisible without the marker", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    const name = `${P}hidden ${Date.now()}`;
    const t = await addTestTitle({ kind: "movie", name });
    await page.goto(`/screening/title/movie/${t.tmdbId}`);
    await page.getByTestId("shelf-want").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    await page.goto("/screening");
    await expect(page.getByText(name)).toHaveCount(1);
    try {
      await context.unroute(`${baseURL}/**`);
      await page.goto("/screening");
      await expect(page.getByTestId("shelf-section-want")).toBeVisible();
      await expect(page.getByText(name)).toHaveCount(0);
      const res = await page.goto(`/screening/title/movie/${t.tmdbId}`);
      expect(res?.status()).toBe(404);
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
