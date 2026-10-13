import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestNewsStory, hasOwner, runPrefix, signIn, storyReactions, useE2eMarker } from "./support/owner";

// News (Chat 3 Phase 3 scope addition), needs db/042.
//
// Logged out: /news is owner-only (smoke.spec covers the redirect).
// Signed in as the owner's REAL account, on TEST stories only (db/042
// add_test_news_story, the e2e marker):
//   * the category chips keep their choice in the URL, through the tabs;
//   * a chip shows only its category (Latest, with the marker);
//   * a Love on /news is a Home reaction (pillar home): it trains Home;
//   * Home: For you's top 10 at most, no tabs, "See all news" to /news; an
//     old Home feed URL lands on /news.
// Test stories never reach a real profile, For you, Wags or the brief.

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
  await signIn(page, "/news");
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

test("the category chips keep their choice in the URL, through the tabs", async () => {
  await page.goto("/news");
  await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
  await page.getByTestId("category-chip-deals").click();
  await expect(page).toHaveURL(/\/news\?category=deals$/);
  await expect(page.getByTestId("category-chip-deals")).toHaveAttribute("aria-current", "page");
  await page.getByTestId("feed-tab-latest").click();
  await expect(page).toHaveURL(/\/news\?category=deals&feed=latest#stories$/);
  await page.getByTestId("category-chip-betting").click();
  await expect(page).toHaveURL(/\/news\?category=betting&feed=latest$/);
  await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(page.getByTestId("category-chip-betting")).toHaveAttribute("aria-current", "page");
  await page.goto("/news?category=gossip");
  await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("news-to-sports")).toHaveAttribute("href", "/sports");
});

test("a chip shows only its category", async () => {
  const stamp = Date.now();
  const deal = `${P}deal chip ${stamp}`;
  const yacht = `${P}yacht chip ${stamp}`;
  await addTestNewsStory({ category: "deals", title: deal, summary: "A test deal.", url: `https://publisher.e2e.invalid/deal-${stamp}` });
  await addTestNewsStory({ category: "luxury_marine", title: yacht, summary: "A test yacht.", url: `https://publisher.e2e.invalid/yacht-${stamp}` });
  const cards = () => page.getByTestId("feed-latest").getByTestId("feed-card");

  await page.goto("/news?category=deals&feed=latest");
  await expect(cards().filter({ hasText: deal })).toHaveCount(1);
  await expect(cards().filter({ hasText: yacht })).toHaveCount(0);

  await page.goto("/news?category=luxury_marine&feed=latest");
  await expect(cards().filter({ hasText: yacht })).toHaveCount(1);
  await expect(cards().filter({ hasText: deal })).toHaveCount(0);

  await page.goto("/news?feed=latest");
  await expect(cards().filter({ hasText: deal })).toHaveCount(1);
  await expect(cards().filter({ hasText: yacht })).toHaveCount(1);
});

test("a Love on /news is a Home reaction", async () => {
  const stamp = Date.now();
  const title = `${P}love on news ${stamp}`;
  const id = await addTestNewsStory({ category: "hospitality", title, summary: "A test story.", url: `https://publisher.e2e.invalid/news-love-${stamp}` });
  await page.goto("/news?category=hospitality&feed=latest");
  const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: title });
  await card.getByTestId("react-love").click();
  await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
  await expect.poll(() => storyReactions(id)).toEqual([{ pillar: "home", reaction: "love" }]);
});

test("Home: For you's top 10, no tabs, and See all news", async () => {
  await page.goto("/");
  const feed = page.getByTestId("home-feed");
  await expect(feed).toBeVisible();
  expect(await feed.getByTestId("feed-card").count()).toBeLessThanOrEqual(10);
  await expect(page.getByTestId("feed-tabs")).toHaveCount(0);
  await feed.getByTestId("see-all-news").click();
  await expect(page).toHaveURL(/\/news$/);
  // An old Home feed URL lands on the same view here.
  await page.goto("/?feed=latest&latest=2");
  await expect(page).toHaveURL(/\/news\?feed=latest&latest=2$/);
});
