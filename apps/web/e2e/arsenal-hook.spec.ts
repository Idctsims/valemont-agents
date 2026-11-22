import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArmRef, addTestSave, addTestTitle, addTestTitleHookCards, arsenalSaves, hasOwner, runPrefix, signIn,
  titleHookCards, titleHookJobs, titleHooks, useE2eMarker,
} from "./support/owner";

// The Screening Room hook (Chat 5 Phase 3; docs/arsenal.md §10; db/062), on a
// TEST title with the marker:
//   * Watched (Finished) writes ONE hook; a TEST title's hook is never queued
//     for the worker (no AI, ever);
//   * a second Watched (after Watching) writes nothing new;
//   * the title page no longer shows the cards (owner, 2026-11-22: the
//     Letterboxd-style page keeps only the trailer and where to watch);
//   * a saved card shows "From <title>" in My Arsenal, linking back, and the
//     "From the Screening Room" filter keeps it and drops a card from
//     elsewhere.
// The cards come from add_test_title_hook_cards (the e2e marker), never the
// model. Test rows are permanent (append-only); names carry the run prefix.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/arsenal-hook.spec.ts --project=phone --output=test-results/arsenal-hook

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const stamp = Date.now();
  const name = `${P}Hook Show ${stamp}`;
  const elsewhere = `${P}Arm card ${stamp}`;
  let title = { id: 0, tmdbId: 0 };
  let context: BrowserContext;
  let page: Page;
  const titlePage = () => `/screening/title/tv/${title.tmdbId}`;

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    title = await addTestTitle({ kind: "tv", name });
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, titlePage());
  });

  test.afterAll(async () => {
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("Watched writes one hook; the title page shows no cards", async () => {
    await page.goto(titlePage());
    await page.getByTestId("title-watched").click();
    await expect(page.getByTestId("title-actions")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await titleHooks(title.id)).map((h) => `${h.status}:${h.is_test}`)).toEqual(["queued:true"]);
    expect(await titleHookJobs(title.id)).toBe(0); // a test title is never handed to the worker
    await page.reload();
    await expect(page.getByTestId("title-arsenal")).toHaveCount(0);
  });

  test("a second Watched (a re-watch) writes nothing new", async () => {
    await page.getByTestId("title-watching").click();
    await expect(page.getByTestId("title-actions")).toHaveAttribute("aria-busy", "false");
    await page.getByTestId("title-watched").click();
    await expect(page.getByTestId("title-actions")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByTestId("title-actions")).toHaveAttribute("data-shelf", "finished");
    expect(await titleHooks(title.id)).toHaveLength(1);
    expect(await titleHookJobs(title.id)).toBe(0);
    // Drafted cards (the e2e helper, never the model), one saved, for My Arsenal below.
    await addTestTitleHookCards(title.id, 3);
    const ids = await titleHookCards(title.id);
    await addTestSave({ refId: ids[1] });
    await expect.poll(async () => (await arsenalSaves(ids[1])).map((s) => `${s.action}:${s.is_test}`)).toEqual(["save:true"]);
  });

  test("My Arsenal: the card says where it came from, and the Screening Room filter keeps it", async () => {
    const arm = await addTestArmRef(elsewhere, "sports", "homies");
    await addTestSave({ refId: arm.id });
    await page.goto("/arsenal/saved");
    const fromTitle = page.getByTestId("saved-item").filter({ has: page.getByTestId("card-from-title") })
      .filter({ hasText: name });
    await expect(fromTitle).toHaveCount(1);
    await expect(fromTitle.getByTestId("card-from-title")).toHaveText(name);
    await expect(fromTitle.getByTestId("card-from-title")).toHaveAttribute("href", titlePage());
    await expect(page.getByTestId("saved-item").filter({ hasText: elsewhere })).toHaveCount(1);

    await page.getByTestId("saved-filter-from-screening").click();
    await expect(fromTitle).toHaveCount(1);
    await expect(page.getByTestId("saved-item").filter({ hasText: elsewhere })).toHaveCount(0);
    for (const item of await page.getByTestId("saved-item").all()) {
      await expect(item.getByTestId("card-from-title")).toHaveCount(1);
    }
    expect((await page.getByTestId("saved-filter-from-screening").boundingBox())!.height).toBeGreaterThanOrEqual(44);

    await fromTitle.getByTestId("card-from-title").click();
    await expect(page).toHaveURL(new RegExp(`${titlePage()}$`));
    await expect(page.getByTestId("title-name")).toHaveText(name);
  });

  test("a long title's From line ends in an ellipsis at 390 px, never clipped", async () => {
    const long = `${P}The Extraordinarily Long and Winding Saga of the Family Business That Would Not Sell ${stamp}`;
    const t = await addTestTitle({ kind: "movie", name: long });
    await page.goto(`/screening/title/movie/${t.tmdbId}`);
    await page.getByTestId("title-watched").click();
    await expect(page.getByTestId("title-actions")).toHaveAttribute("aria-busy", "false");
    await addTestTitleHookCards(t.id, 1);
    const [cardId] = await titleHookCards(t.id);
    await addTestSave({ refId: cardId });

    expect(page.viewportSize()!.width).toBe(390);
    await page.goto("/arsenal/saved");
    const item = page.locator(`[data-testid="saved-item"][data-id="${cardId}"]`);
    const name = item.getByTestId("card-from-title-name");
    await expect(item.getByTestId("card-from-title")).toHaveText(long); // the whole name is still there
    await expect(item.getByTestId("card-from-title")).toHaveAttribute("title", long);
    const box = await name.evaluate((el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const card = el.closest('[data-testid="saved-item"]')!.getBoundingClientRect();
      return { overflow: s.textOverflow, wrap: s.whiteSpace, hidden: s.overflowX, cut: el.scrollWidth > el.clientWidth,
               right: r.right, cardRight: card.right, page: document.documentElement.scrollWidth };
    });
    expect(box.overflow).toBe("ellipsis");
    expect(box.wrap).toBe("nowrap");
    expect(box.hidden).toBe("hidden");
    expect(box.cut).toBe(true); // long enough that it really truncates
    expect(box.right).toBeLessThanOrEqual(box.cardRight + 0.5);
    expect(box.right).toBeLessThanOrEqual(390);
    expect(box.page).toBeLessThanOrEqual(390); // no horizontal page scroll
  });
});
