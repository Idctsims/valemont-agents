import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArmRef, addTestArsenalDrop, addTestArsenalRef, addTestArsenalWord, addTestSave, arsenalSaves, hasOwner,
  runPrefix, signIn, useE2eMarker,
} from "./support/owner";

// My Arsenal (Chat 5 Phase 2; docs/arsenal.md §9.4; db/059), on TEST rows
// with the marker:
//   * Save on a drop card and on the Power Word writes a TEST save, and My
//     Arsenal lists it; Unsave leaves a line with Undo, and Undo saves again
//     (save, unsave, save on the record);
//   * a saved Arm Me card shows "Checking" until the verifier settles it;
//   * the room and category filters narrow the list (a category shows
//     references only);
//   * test saves are invisible without the marker.
// Test rows are permanent (append-only); names carry the run prefix.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/arsenal-saved.spec.ts --project=phone --output=test-results/arsenal-saved

test.describe("logged out", () => {
  test("My Arsenal asks for sign-in", async ({ page }) => {
    await page.goto("/arsenal/saved");
    await expect(page).toHaveURL(/\/login\?next=%2Farsenal%2Fsaved/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const stamp = Date.now();
  const titles = [`${P}Rubicon ${stamp}`, `${P}Medici ${stamp}`, `${P}Last Dance ${stamp}`];
  const lemma = `${P}optionality ${stamp}`.toLowerCase();
  const armTitle = `${P}arm card ${stamp}`;
  let ids: number[] = [];
  let wordId = 0;
  let armId = 0;
  let context: BrowserContext;
  let page: Page;

  const item = (title: string) => page.getByTestId("saved-item").filter({ hasText: title });

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    ids = [
      await addTestArsenalRef({ category: "history_military", title: titles[0], room: "boardroom" }),
      await addTestArsenalRef({ category: "finance_business", title: titles[1], room: "pitch" }),
      await addTestArsenalRef({ category: "film_tv", title: titles[2], room: "homies", quote: "Show me the money." }),
    ];
    wordId = await addTestArsenalWord(lemma);
    await addTestArsenalDrop(ids, wordId);
    armId = (await addTestArmRef(armTitle, "sports", "negotiation")).id;
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

  test("Save on a drop card lands in My Arsenal; Unsave leaves Undo; Undo saves again", async () => {
    await page.goto("/arsenal");
    const card = page.getByTestId("arsenal-card").filter({ hasText: titles[0] });
    await card.getByTestId("card-save").click();
    await expect(card.getByTestId("card-save")).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => (await arsenalSaves(ids[0])).map((s) => `${s.action}:${s.is_test}`)).toEqual(["save:true"]);

    await page.goto("/arsenal/saved");
    await expect(item(titles[0])).toHaveCount(1);
    await expect(item(titles[0]).getByTestId("card-check")).toHaveText("Checked");
    await item(titles[0]).getByTestId("saved-unsave").click();
    const removed = page.getByTestId("saved-removed").filter({ hasText: titles[0] });
    await expect(removed).toHaveText(new RegExp(`Removed ${titles[0]}`));
    await expect(page.getByTestId("saved-list")).toHaveAttribute("aria-busy", "false");
    await removed.getByTestId("saved-undo").click();
    await expect(item(titles[0])).toHaveCount(1);
    await expect(page.getByTestId("saved-list")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await arsenalSaves(ids[0])).map((s) => s.action)).toEqual(["save", "unsave", "save"]);
  });

  test("the Power Word saves too, and unsaves with Undo", async () => {
    await page.goto("/arsenal");
    await page.getByTestId("word-save").click();
    await expect(page.getByTestId("word-save")).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => (await arsenalSaves(wordId, "word_id")).map((s) => s.action)).toEqual(["save"]);
    await page.goto("/arsenal/saved");
    const word = item(lemma);
    await expect(word.getByTestId("card-title")).toHaveText(lemma);
    await word.getByTestId("saved-unsave").click();
    await page.getByTestId("saved-removed").filter({ hasText: lemma }).getByTestId("saved-undo").click();
    await expect(item(lemma)).toHaveCount(1);
    await expect.poll(async () => (await arsenalSaves(wordId, "word_id")).map((s) => s.action)).toEqual(["save", "unsave", "save"]);
  });

  test("a saved Arm Me card says Checking", async () => {
    await addTestSave({ refId: armId });
    await page.goto("/arsenal/saved");
    await expect(item(armTitle).getByTestId("card-check")).toHaveText("Checking");
  });

  test("the room and category filters narrow the list", async () => {
    await addTestSave({ refId: ids[2] });
    await page.goto("/arsenal/saved");
    await page.getByTestId("saved-filter-room-homies").click();
    await expect(item(titles[2])).toHaveCount(1);
    await expect(item(titles[0])).toHaveCount(0);
    for (const room of await page.getByTestId("saved-item").getByTestId("room-chip").allTextContents()) expect(room).toBe("Homies");
    await page.getByTestId("saved-filter-room-all").click();
    await page.getByTestId("saved-filter-category-history_military").click();
    await expect(item(titles[0])).toHaveCount(1);
    await expect(item(titles[2])).toHaveCount(0);
    await expect(item(lemma)).toHaveCount(0); // a word has no category
    for (const c of await page.getByTestId("saved-item").getByTestId("card-category").allTextContents()) expect(c).toBe("History & war");
    // Each filter chip is a 44 px target.
    const chip = (await page.getByTestId("saved-filter-room-pitch").boundingBox())!;
    expect(chip.height).toBeGreaterThanOrEqual(44);
  });

  test("test saves are invisible without the marker", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    try {
      await context.unroute(`${baseURL}/**`);
      await page.goto("/arsenal/saved");
      await expect(page.getByRole("heading", { name: "My Arsenal" })).toBeVisible();
      for (const t of [...titles, armTitle, lemma]) await expect(page.getByText(t)).toHaveCount(0);
      await expect(page.getByText(P, { exact: false })).toHaveCount(0);
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
