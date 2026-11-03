import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArmRef, addTestSave, addTestTitle, addTestTitleHookCards, arsenalSaves, hasOwner, runPrefix, signIn,
  titleHookCards, titleHookJobs, titleHooks, useE2eMarker,
} from "./support/owner";

// The Screening Room hook (Chat 5 Phase 3; docs/arsenal.md §10; db/062), on a
// TEST title with the marker:
//   * Finished writes ONE hook (the title page says "Drafting…"); a TEST
//     title's hook is never queued for the worker (no AI, ever);
//   * a second Finished (after Watching) writes nothing new;
//   * the drafted cards render on the title page with their tags (Checked,
//     and one Unverified with its reason) and Save, plus the Power Word;
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

  test("Finished writes one hook and the page says Drafting", async () => {
    await page.goto(titlePage());
    await expect(page.getByTestId("title-arsenal")).toHaveCount(0);
    await page.getByTestId("shelf-finished").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    await expect.poll(async () => (await titleHooks(title.id)).map((h) => `${h.status}:${h.is_test}`)).toEqual(["queued:true"]);
    expect(await titleHookJobs(title.id)).toBe(0); // a test title is never handed to the worker
    await page.reload();
    await expect(page.getByTestId("title-arsenal")).toHaveAttribute("data-state", "queued");
    await expect(page.getByTestId("title-arsenal-drafting")).toContainText("Drafting");
  });

  test("a second Finished (a re-watch) writes nothing new", async () => {
    await page.getByTestId("shelf-watching").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    await page.getByTestId("shelf-finished").click();
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
    await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("data-shelf", "finished");
    expect(await titleHooks(title.id)).toHaveLength(1);
    expect(await titleHookJobs(title.id)).toBe(0);
  });

  test("the drafted cards render on the title page with their tags and Save", async () => {
    await addTestTitleHookCards(title.id, 3);
    await page.goto(titlePage());
    const section = page.getByTestId("title-arsenal");
    await expect(section).toHaveAttribute("data-state", "drafted");
    const cards = section.getByTestId("title-arsenal-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0).getByTestId("card-check")).toHaveText("Unverified");
    await expect(cards.nth(0)).toContainText("a test reason");
    await expect(cards.nth(1).getByTestId("card-check")).toHaveText("Checked");
    await expect(section.getByTestId("title-arsenal-word")).toHaveCount(1);
    await cards.nth(1).getByTestId("card-save").click();
    await expect(cards.nth(1).getByTestId("card-save")).toHaveAttribute("aria-pressed", "true");
    const ids = await titleHookCards(title.id);
    await expect.poll(async () => (await arsenalSaves(ids[1])).map((s) => `${s.action}:${s.is_test}`)).toEqual(["save:true"]);
    // Each Save is a 44 px target.
    expect((await cards.nth(1).getByTestId("card-save").boundingBox())!.height).toBeGreaterThanOrEqual(44);
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
    await expect(page.getByTestId("title-arsenal")).toBeVisible();
  });
});
