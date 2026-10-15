import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestRec, addTestSeenTitle, addTestStory, addTestTitle, hasOwner, retireTestRec, runPrefix, signIn, titleReactions,
  useE2eMarker,
} from "./support/owner";

// Screening Room recommendations (Chat 4 Phase 4; db/053). Needs db/053.
//
// Logged out: the seen-it pass is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/053
// add_test_rec / add_test_seen_title, db/051 add_test_title, db/032
// add_test_story: is_test, the e2e marker):
//   * the For you row renders a pick with "Because you loved";
//   * Not interested removes the card, and it does not return on refresh;
//   * the seen-it flow marks a title seen, and it never appears in recs;
//   * a title page's reaction is pressed and persists; a Want title's page
//     shows "In the news" for a Home story that names it;
//   * a test pick is invisible without the marker.
// Test rows are permanent (append-only). Every test pick this spec makes is
// retired (Not interested) before it ends, so later runs' For you stays clear.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/screening-recs.spec.ts --project=phone --output=test-results/screening-recs

test.describe("logged out", () => {
  test("the seen-it pass asks for sign-in", async ({ page }) => {
    await page.goto("/screening/seen");
    await expect(page).toHaveURL(/\/login\?next=%2Fscreening%2Fseen/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const made: number[] = [];
  let context: BrowserContext;
  let page: Page;

  async function rec(kind: "movie" | "tv", label: string) {
    const stamp = Date.now();
    const name = `${P}${label} ${stamp}`;
    const because = `${P}${label} loved ${stamp}`;
    const id = await addTestRec({ kind, name, because });
    made.push(id);
    return { id, name, because };
  }

  const card = (name: string) => page.getByTestId("for-you").getByTestId("for-you-card").filter({ hasText: name });

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/screening");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    // Retire every pick this run made (a second Not interested is harmless).
    for (const id of made) await retireTestRec(id);
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("the For you row shows a pick with Because you loved", async () => {
    const r = await rec("movie", "pick");
    await page.goto("/screening");
    await expect(card(r.name)).toHaveCount(1);
    await expect(card(r.name).getByTestId("rec-because")).toHaveText(`Because you loved ${r.because}`);
    // For you sits above the shelves.
    const [forYou, want] = await Promise.all([
      page.getByTestId("for-you").boundingBox(), page.getByTestId("shelf-section-want").boundingBox(),
    ]);
    expect(forYou!.y).toBeLessThan(want!.y);
  });

  test("Not interested removes a card, and it does not return on refresh", async () => {
    const r = await rec("tv", "skip");
    await page.goto("/screening");
    await expect(card(r.name)).toHaveCount(1);
    await card(r.name).getByTestId("rec-not-interested").click();
    await expect(card(r.name)).toHaveCount(0);
    await expect(page.getByTestId("for-you-toast")).toContainText(`Not interested: ${r.name}`);
    await expect(page.getByTestId("for-you-list")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => titleReactions(r.id)).toEqual([{ reaction: "not_interested", seen: false, origin: "card" }]);
    await page.reload();
    await expect(page.getByTestId("for-you")).toBeVisible();
    await expect(card(r.name)).toHaveCount(0);
  });

  test("the seen-it flow marks a title seen, and it never appears in recs", async () => {
    const name = `${P}seen ${Date.now()}`;
    const id = await addTestSeenTitle({ kind: "movie", name });
    made.push(id);
    await page.goto("/screening");
    await expect(card(name)).toHaveCount(1);
    // Newest test title first in the pass, with the marker.
    await page.goto("/screening/seen?again=1");
    await expect(page.getByTestId("seen-title")).toHaveText(name);
    await page.getByTestId("seen-loved").click();
    await expect(page.getByTestId("seen-pass")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByTestId("seen-error")).toHaveCount(0);
    await expect.poll(() => titleReactions(id)).toEqual([{ reaction: "love", seen: true, origin: "seen_pass" }]);
    await page.goto("/screening");
    await expect(page.getByTestId("for-you")).toBeVisible();
    await expect(card(name)).toHaveCount(0);
    // Resuming skips it: the pass opens on a title not yet answered (or done).
    await page.goto("/screening/seen");
    await expect(page.getByTestId("seen-pass")).toBeVisible();
    await expect(page.getByTestId("seen-title")).not.toHaveText(name);
  });

  test("a title page's reaction persists, and a Want title shows In the news", async () => {
    const stamp = Date.now();
    const name = `${P}newsworthy ${stamp}`;
    const t = await addTestTitle({ kind: "tv", name });
    await page.goto(`/screening/title/tv/${t.tmdbId}`);
    await expect(page.getByTestId("title-name")).toHaveText(name);
    // Not on a shelf yet: no news section, whatever Home says.
    await expect(page.getByTestId("title-news")).toHaveCount(0);
    await page.getByTestId("title-love").click();
    await expect(page.getByTestId("title-reactions")).toHaveAttribute("aria-busy", "false");
    await page.getByTestId("shelf-want").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    const headline = `Inside ${name}: the season everyone is talking about`;
    await addTestStory({ pillar: "home", title: headline, summary: null, url: `https://news.e2e.invalid/${stamp}` });
    await addTestStory({ pillar: "home", title: `${P}unrelated ${stamp}`, summary: null, url: `https://news.e2e.invalid/u-${stamp}` });
    await page.reload();
    await expect(page.getByTestId("title-reactions")).toHaveAttribute("data-reaction", "love");
    await expect(page.getByTestId("title-love")).toHaveAttribute("aria-pressed", "true");
    const news = page.getByTestId("title-news-item");
    await expect(news).toHaveCount(1);
    await expect(news).toContainText(headline);
    await expect.poll(() => titleReactions(t.id)).toEqual([{ reaction: "love", seen: false, origin: "title" }]);
    // Tapping the pressed reaction takes it back.
    await page.getByTestId("title-love").click();
    await expect(page.getByTestId("title-reactions")).toHaveAttribute("data-reaction", "none");
    await expect(page.getByTestId("title-reactions")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await titleReactions(t.id)).map((r) => r.reaction)).toEqual(["love", "none"]);
  });

  test("a test pick is invisible without the marker", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    const r = await rec("movie", "hidden");
    await page.goto("/screening");
    await expect(card(r.name)).toHaveCount(1);
    try {
      await context.unroute(`${baseURL}/**`);
      await page.goto("/screening");
      await expect(page.getByTestId("screening-tabs")).toBeVisible();
      await expect(page.getByText(r.name)).toHaveCount(0);
      await expect(page.getByText(r.because)).toHaveCount(0);
      await page.goto("/screening/seen");
      await expect(page.getByText(P, { exact: false })).toHaveCount(0);
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
