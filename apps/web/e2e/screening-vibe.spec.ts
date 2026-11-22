import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { hasOwner, runPrefix, signIn, useE2eMarker } from "./support/owner";

// More like… (owner, 2026-11-21; docs/screening.md §8), on the mock model
// (lib/screening/vibe-mock.ts: Troy, Kingdom of Heaven, an invented title,
// Gladiator), matched on live TMDB. Every search here carries the verified
// e2e marker, so its row is is_test and no cent is spent.

test.describe("logged out", () => {
  test("the vibe and anchor routes ask for sign-in", async ({ request }) => {
    const vibe = await request.post("/api/screening/vibe", { data: { anchor: { kind: "text", label: "heists" } }, maxRedirects: 0 });
    expect(vibe.status()).not.toBe(200);
    const anchors = await request.get("/api/screening/anchors?q=heat", { maxRedirects: 0 });
    expect(anchors.status()).not.toBe(200);
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

  const cards = () => page.getByTestId("vibe-card");

  test("a phrase: the verified picks only, each with its reason; the invented one never shows", async () => {
    await page.goto("/screening");
    await page.getByTestId("vibe-input").fill(`${P}sword and sandal epics`);
    await page.getByTestId("vibe-input").press("Enter");
    await expect(page.getByTestId("vibe-results")).toHaveAttribute("data-cached", "false", { timeout: 30_000 });
    await expect(cards().filter({ hasText: "Troy" })).toHaveCount(1);
    await expect(cards().filter({ hasText: "Kingdom of Heaven" })).toHaveCount(1);
    await expect(cards().filter({ hasText: "Never Made" })).toHaveCount(0);
    await expect(cards().first().getByTestId("vibe-reason")).toHaveText("Ancient epic, brutal duels, tragic heroes");
  });

  test("the same phrase again is the saved search, and a recent chip", async () => {
    await page.goto("/screening");
    await expect(page.getByTestId("vibe-recent")).toContainText(`${P}sword and sandal epics`);
    await page.getByTestId("vibe-input").fill(`${P}sword and sandal epics`);
    await page.getByTestId("vibe-input").press("Enter");
    await expect(page.getByTestId("vibe-results")).toHaveAttribute("data-cached", "true");
    await expect(cards().filter({ hasText: "Troy" })).toHaveCount(1);
  });

  test("More like this on Gladiator's page never lists Gladiator itself", async () => {
    await page.goto("/screening/title/movie/98");
    await page.getByTestId("title-more-like").click();
    await expect(page.getByTestId("vibe-results")).toBeVisible({ timeout: 30_000 });
    await expect(cards().filter({ hasText: "Troy" })).toHaveCount(1);
    await expect(page.locator('[data-testid="vibe-card"][data-tmdb="movie:98"]')).toHaveCount(0);
  });

  test("typing suggests titles and people", async () => {
    await page.goto("/screening");
    await page.getByTestId("vibe-input").pressSequentially("ridley scott", { delay: 30 });
    await expect(page.locator('[data-testid="vibe-suggestion"][data-type="person"]').first()).toContainText("Ridley Scott");
    // The list never sits over the page: Escape closes it, and so does a tap elsewhere.
    await page.getByTestId("vibe-input").press("Escape");
    await expect(page.getByTestId("vibe-suggestions")).toHaveCount(0);
    await page.getByTestId("vibe-input").pressSequentially(" ", { delay: 30 });
    await expect(page.getByTestId("vibe-suggestions")).toBeVisible();
    await page.getByTestId("for-you").click({ position: { x: 5, y: 5 } });
    await expect(page.getByTestId("vibe-suggestions")).toHaveCount(0);
  });
});
