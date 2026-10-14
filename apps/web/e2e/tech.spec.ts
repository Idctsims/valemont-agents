import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestTechStory, hasOwner, runPrefix, signIn, storyReactions, useE2eMarker } from "./support/owner";

// Tech Hub (Chat 4 Phase 1; docs/tech_hub.md), needs db/045.
//
// Logged out: /tech and a story link are owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/045
// add_test_tech_story: is_test, from the official test source, the e2e
// marker):
//   * the chips keep their choice in the URL, through the tabs;
//   * a chip shows only its category; the card carries the Official tag;
//   * pillar separation: a Love on /tech writes pillar 'tech' only;
//   * no tech story on /news (either tab), on Home or on /sports;
//   * a test story is invisible without the marker, and has no story page.
// Test stories never reach a real profile, For you, Wags or the brief; the
// rows written here stay (append-only), each named with this run's prefix.
//
// Run (owner, CLAUDE.md §6):
//   pnpm test:e2e e2e/tech.spec.ts --project=phone --output=test-results/tech

test.describe("logged out", () => {
  test("/tech asks for sign-in", async ({ page }) => {
    await page.goto("/tech?category=tooling");
    await expect(page).toHaveURL(/\/login\?next=%2Ftech/);
  });

  test("a story link asks for sign-in", async ({ page }) => {
    await page.goto("/tech/story/1");
    await expect(page).toHaveURL(/\/login\?next=%2Ftech%2Fstory%2F1/);
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
    await signIn(page, "/tech");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("the chips keep their choice in the URL, through the tabs", async () => {
    await page.goto("/tech");
    await expect(page.getByTestId("category-chips").getByRole("link")).toHaveText([
      "All", "Models & features", "Pricing & credits", "Tooling", "Agents", "Stack", "Community",
    ]);
    await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("category-chip-pricing_credits").click();
    await expect(page).toHaveURL(/\/tech\?category=pricing_credits$/);
    await expect(page.getByTestId("category-chip-pricing_credits")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("feed-tab-latest").click();
    await expect(page).toHaveURL(/\/tech\?category=pricing_credits&feed=latest#stories$/);
    await expect(page.getByTestId("category-chip-pricing_credits")).toHaveAttribute("aria-current", "page");
    // Changing the chip keeps the tab.
    await page.getByTestId("category-chip-tooling").click();
    await expect(page).toHaveURL(/\/tech\?category=tooling&feed=latest$/);
    await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
    await page.reload();
    await expect(page.getByTestId("category-chip-tooling")).toHaveAttribute("aria-current", "page");
    // A Home category, or Phase 2's repos, is All here.
    await page.goto("/tech?category=deals");
    await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
  });

  test("a chip shows only its category, and the card is Official", async () => {
    const stamp = Date.now();
    const credits = `${P}credits chip ${stamp}`;
    const tooling = `${P}tooling chip ${stamp}`;
    await addTestTechStory({ category: "pricing_credits", title: credits, summary: "A credits test story.", url: `https://publisher.e2e.invalid/credits-${stamp}` });
    await addTestTechStory({ category: "tooling", title: tooling, summary: null, url: `https://publisher.e2e.invalid/tooling-${stamp}` });
    const cards = () => page.getByTestId("feed-latest").getByTestId("feed-card");

    await page.goto("/tech?category=pricing_credits&feed=latest");
    await expect(cards().filter({ hasText: credits })).toHaveCount(1);
    await expect(cards().filter({ hasText: tooling })).toHaveCount(0);
    await expect(cards().filter({ hasText: credits }).getByTestId("feed-official")).toHaveText("Official");

    await page.goto("/tech?category=tooling&feed=latest");
    const headlineOnly = cards().filter({ hasText: tooling });
    await expect(headlineOnly).toHaveCount(1);
    // No summary yet: the headline and the Official tag, never hidden.
    await expect(headlineOnly.getByTestId("feed-summary")).toHaveCount(0);
    await expect(headlineOnly.getByTestId("feed-official")).toBeVisible();
    await expect(cards().filter({ hasText: credits })).toHaveCount(0);

    await page.goto("/tech?feed=latest");
    await expect(cards().filter({ hasText: credits })).toHaveCount(1);
    await expect(cards().filter({ hasText: tooling })).toHaveCount(1);
  });

  test("a Love on Tech is a tech reaction, never Home's or Sports'", async () => {
    const stamp = Date.now();
    const title = `${P}love on tech ${stamp}`;
    const id = await addTestTechStory({ category: "agents", title, summary: "A test story.", url: `https://publisher.e2e.invalid/love-${stamp}` });
    await page.goto("/tech?category=agents&feed=latest");
    const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: title });
    await card.getByTestId("react-love").click();
    await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => storyReactions(id)).toEqual([{ pillar: "tech", reaction: "love" }]);
  });

  test("no tech story on /news, Home or /sports", async () => {
    const stamp = Date.now();
    const title = `${P}never on news ${stamp}`;
    await addTestTechStory({ category: "models_features", title, summary: "A test story.", url: `https://publisher.e2e.invalid/never-${stamp}` });
    // The marker shows test stories in Latest, so Latest is the real check:
    // even a visible test tech story never reaches these pages.
    await page.goto("/tech?feed=latest");
    await expect(page.getByText(title)).toHaveCount(1);
    for (const url of ["/news?feed=latest", "/news", "/", "/sports?feed=latest", "/sports"]) {
      await page.goto(url);
      await expect(page.getByTestId("feed-card").first().or(page.getByTestId("feed-empty"))).toBeVisible();
      await expect(page.getByText(title), url).toHaveCount(0);
    }
  });

  test("a test story is invisible without the marker, and has no story page", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    const stamp = Date.now();
    const title = `${P}hidden ${stamp}`;
    const id = await addTestTechStory({ category: "stack", title, summary: "A test story.", url: `https://publisher.e2e.invalid/hidden-${stamp}` });
    try {
      // Same signed-in context, marker off (a second sign-in trips Supabase Auth's limit).
      await context.unroute(`${baseURL}/**`);
      await page.goto("/tech?feed=latest");
      await expect(page.getByTestId("feed-card").first().or(page.getByTestId("feed-empty"))).toBeVisible();
      await expect(page.getByText(title)).toHaveCount(0);
      await page.goto("/tech?category=stack");
      await expect(page.getByText(title)).toHaveCount(0);
    } finally {
      await useE2eMarker(context, baseURL);
    }
    // The push's deep link reads real stories only: a test story is a 404.
    const res = await page.goto(`/tech/story/${id}`);
    expect(res?.status()).toBe(404);
  });
});
