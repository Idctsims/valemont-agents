import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArsenalDrop, addTestArsenalRef, addTestArsenalWord, arsenalReactions, hasOwner, runPrefix, signIn,
  useE2eMarker,
} from "./support/owner";

// The Arsenal (Chat 5 Phase 1; db/054). Needs db/054.
//
// Logged out: /arsenal is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/054
// add_test_arsenal_ref / _word / _drop: is_test, the e2e marker):
//   * a test drop renders the Power Word and every card field, in slot order;
//   * a reaction writes, reads back (a TEST reaction: is_test from its
//     target, so it never trains the real weights) and persists;
//   * Already know it hides that reference into the "Already known" fold,
//     and Undo brings it back;
//   * every reaction button shows its short label (Love / Not for me /
//     Know it), keeps its accessible name and is at least 44 px;
//   * the Power Word's Already know it does the same: hidden into the
//     fold, still hidden after a reload, Undo restores it;
//   * the Unverified fold shows the reason, never a Checked tag;
//   * a test drop is invisible without the marker.
// Test rows are permanent (append-only); names carry the run prefix.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/arsenal.spec.ts --project=phone --output=test-results/arsenal

test.describe("logged out", () => {
  test("the Arsenal asks for sign-in", async ({ page }) => {
    await page.goto("/arsenal");
    await expect(page).toHaveURL(/\/login\?next=%2Farsenal/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const stamp = Date.now();
  const titles = [`${P}Rubicon ${stamp}`, `${P}Medici ${stamp}`, `${P}Last Dance ${stamp}`];
  const lemma = `${P}inflection ${stamp}`.toLowerCase();
  let ids: number[] = [];
  let wordId = 0;
  let context: BrowserContext;
  let page: Page;

  const card = (title: string) => page.getByTestId("arsenal-card").filter({ hasText: title });

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    ids = [
      await addTestArsenalRef({ category: "history_military", title: titles[0], room: "boardroom",
                                quote: "The die is cast." }),
      await addTestArsenalRef({ category: "finance_business", title: titles[1], room: "pitch", verification: "corrected",
                                note: "the date was 1434, not 1397" }),
      await addTestArsenalRef({ category: "sports", title: titles[2], room: "homies" }),
    ];
    wordId = await addTestArsenalWord(lemma);
    await addTestArsenalDrop(ids, wordId);
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/arsenal");
  });

  test.afterAll(async () => {
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("a drop renders the Power Word and every card field, in slot order", async () => {
    await page.goto("/arsenal");
    await expect(page.getByTestId("power-word-lemma")).toHaveText(lemma);
    await expect(page.getByTestId("power-word-ipa")).toHaveText("/ˈtɛst/");
    await expect(page.getByTestId("power-word-sentence")).toContainText("test word");
    await expect(page.getByTestId("power-word-etymology")).toContainText("From the test suite");
    await expect(page.getByTestId("arsenal-card").getByTestId("card-title")).toHaveText(titles);
    const first = card(titles[0]);
    await expect(first.getByTestId("card-category")).toHaveText("History & war");
    await expect(first.getByTestId("card-source-work")).toHaveText("Test work (2026)");
    await expect(first.getByTestId("card-origin")).toContainText(`Where ${titles[0]} comes from`);
    await expect(first.getByTestId("card-quote")).toContainText("“The die is cast.”");
    await expect(first.getByTestId("card-meaning")).toHaveText("What it means when you use it.");
    await expect(first.getByTestId("card-deploy")).toHaveText("The line you would actually say in the room.");
    await expect(first.getByTestId("room-chip")).toHaveText("Boardroom");
    await expect(first.getByTestId("card-source")).toHaveAttribute("href", /^https:\/\/e2e\.invalid\/arsenal\//);
    await expect(first.getByTestId("card-check")).toHaveText("Checked");
    await expect(card(titles[1]).getByTestId("card-check")).toHaveText("Corrected");
    await expect(card(titles[2]).getByTestId("room-chip")).toHaveText("Homies");
  });

  test("a reaction writes, reads back as a test reaction, and persists", async () => {
    await page.goto("/arsenal");
    await card(titles[0]).getByTestId("card-love").click();
    await expect(card(titles[0]).getByTestId("card-love")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByTestId("arsenal-error")).toHaveCount(0);
    await expect.poll(() => arsenalReactions(ids[0])).toEqual([{ reaction: "love", is_test: true }]);
    await page.reload();
    await expect(card(titles[0]).getByTestId("card-reactions")).toHaveAttribute("data-reaction", "love");
    // Tapping it again takes it back.
    await card(titles[0]).getByTestId("card-love").click();
    await expect(card(titles[0]).getByTestId("card-reactions")).toHaveAttribute("data-reaction", "none");
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await arsenalReactions(ids[0])).map((r) => r.reaction)).toEqual(["love", "none"]);
  });

  test("Already know it hides the reference, and Undo brings it back", async () => {
    await page.goto("/arsenal");
    await card(titles[1]).getByTestId("card-known").click();
    await expect(card(titles[1])).toHaveCount(0);
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => arsenalReactions(ids[1])).toEqual([{ reaction: "known", is_test: true }]);
    await page.reload();
    await expect(page.getByTestId("power-word")).toBeVisible();
    await expect(card(titles[1])).toHaveCount(0);
    await expect(page.getByTestId("arsenal-card")).toHaveCount(2);
    const fold = page.getByTestId("arsenal-known");
    await fold.getByRole("button", { name: /Already known/ }).click();
    await expect(fold.getByTestId("known-ref")).toHaveText(new RegExp(titles[1]));
    await fold.getByTestId("known-undo").click();
    await expect(card(titles[1])).toHaveCount(1);
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await arsenalReactions(ids[1])).map((r) => r.reaction)).toEqual(["known", "none"]);
  });

  test("every reaction button shows a short label and keeps its accessible name, at 44 px", async () => {
    await page.goto("/arsenal");
    for (const prefix of ["word", "card"]) {
      const group = page.getByTestId(`${prefix}-reactions`).first();
      for (const [id, name, short] of [["love", "Love", "Love"], ["not-interested", "Not interested", "Not for me"],
                                       ["known", "Already know it", "Know it"]]) {
        const b = group.getByTestId(`${prefix}-${id}`);
        await expect(b).toHaveAccessibleName(name);
        await expect(b).toContainText(short);
        const box = (await b.boundingBox())!;
        expect(box.width).toBeGreaterThanOrEqual(44);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
    }
  });

  test("Already know it on the Power Word hides it into the fold, and Undo brings it back", async () => {
    await page.goto("/arsenal");
    await page.getByTestId("word-known").click();
    await expect(page.getByTestId("power-word")).toHaveCount(0);
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByTestId("arsenal-error")).toHaveCount(0);
    await expect.poll(() => arsenalReactions(wordId, "word_id")).toEqual([{ reaction: "known", is_test: true }]);
    // Excluded after a reload too, like a known reference; the cards stay.
    await page.reload();
    await expect(page.getByTestId("arsenal-card").first()).toBeVisible();
    await expect(page.getByTestId("power-word")).toHaveCount(0);
    const fold = page.getByTestId("arsenal-known");
    await fold.getByRole("button", { name: /Already known/ }).click();
    await expect(fold.getByTestId("known-word")).toContainText(lemma);
    await fold.getByTestId("known-word").getByTestId("known-undo").click();
    await expect(page.getByTestId("power-word-lemma")).toHaveText(lemma);
    await expect(page.getByTestId("arsenal-drop")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await arsenalReactions(wordId, "word_id")).map((r) => r.reaction))
      .toEqual(["known", "none"]);
  });

  test("the Unverified fold shows the reason, never a Checked tag", async () => {
    const title = `${P}unverified ${stamp}`;
    await addTestArsenalRef({ category: "film_tv", title, room: "pitch", verification: "unverified",
                              note: "contradicted: the source names a different film" });
    await page.goto("/arsenal");
    const fold = page.getByTestId("arsenal-unverified");
    await fold.getByRole("button", { name: /Unverified/ }).click();
    const row = fold.getByTestId("unverified-ref").filter({ hasText: title });
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("unverified-reason")).toHaveText("Not checked: contradicted: the source names a different film");
    await expect(row.getByTestId("card-check")).toHaveCount(0);
    // Never one of today's cards.
    await expect(card(title)).toHaveCount(0);
  });

  test("a test drop is invisible without the marker", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    try {
      await context.unroute(`${baseURL}/**`);
      await page.goto("/arsenal");
      await expect(page.getByRole("heading", { name: "The Arsenal" })).toBeVisible();
      for (const t of titles) await expect(page.getByText(t)).toHaveCount(0);
      await expect(page.getByText(lemma)).toHaveCount(0);
      await expect(page.getByText(P, { exact: false })).toHaveCount(0);
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
