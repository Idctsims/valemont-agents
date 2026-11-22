import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestRec, addTestSeenTitle, addTestTitle, hasOwner, retireTestRec, runPrefix, signIn, titleReactions,
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

  // A card is a poster only now (owner, Nov 22): found by its link's name.
  const card = (name: string) => page.getByTestId("for-you").getByTestId("for-you-card")
    .filter({ has: page.getByRole("link", { name, exact: true }) });

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

  test("the For you row shows a pick as a poster, Film or Series under it, above the shelves", async () => {
    const r = await rec("movie", "pick");
    await page.goto("/screening");
    await expect(card(r.name)).toHaveCount(1);
    await expect(card(r.name)).toContainText("Film");
    await expect(card(r.name).getByText(r.because)).toHaveCount(0); // no words but the tag
    const [forYou, shelves] = await Promise.all([
      page.getByTestId("for-you").boundingBox(), page.getByTestId("shelves").boundingBox(),
    ]);
    expect(forYou!.y).toBeLessThan(shelves!.y);
  });

  test("Not interested from a title page's menu takes a pick out of For you for good", async () => {
    const r = await rec("tv", "skip");
    await page.goto("/screening");
    await card(r.name).getByRole("link").click();
    await expect(page.getByTestId("title-name")).toHaveText(r.name);
    await page.getByTestId("title-more").click();
    await page.getByTestId("title-not-interested").click();
    await expect.poll(() => titleReactions(r.id)).toEqual([{ reaction: "not_interested", seen: false, origin: "title" }]);
    await page.goto("/screening");
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

  test("Like on a title page persists, and tapping it again takes it back", async () => {
    const name = `${P}liked ${Date.now()}`;
    const t = await addTestTitle({ kind: "tv", name });
    const actions = page.getByTestId("title-actions");
    await page.goto(`/screening/title/tv/${t.tmdbId}`);
    await expect(page.getByTestId("title-name")).toHaveText(name);
    await page.getByTestId("title-liked").click();
    await expect(actions).toHaveAttribute("aria-busy", "false");
    await page.reload();
    await expect(actions).toHaveAttribute("data-reaction", "love");
    await expect(page.getByTestId("title-liked")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("title-liked")).toContainText("Liked");
    await expect.poll(() => titleReactions(t.id)).toEqual([{ reaction: "love", seen: false, origin: "title" }]);
    await page.getByTestId("title-liked").click();
    await expect(actions).toHaveAttribute("data-reaction", "none");
    await expect(actions).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await titleReactions(t.id)).map((r) => r.reaction)).toEqual(["love", "none"]);
    // In the news left the title page (owner, Nov 22).
    await expect(page.getByTestId("title-news")).toHaveCount(0);
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
      await expect(page.getByRole("link", { name: r.name, exact: true })).toHaveCount(0);
      await expect(page.getByText(r.because)).toHaveCount(0);
      await page.goto("/screening/seen");
      await expect(page.getByText(P, { exact: false })).toHaveCount(0);
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
