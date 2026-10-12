import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestMoveProposal,
  addTestSportsStory,
  hasOwner,
  runPrefix,
  signIn,
  storyReactions,
  useE2eMarker,
} from "./support/owner";

// Sports News (Chat 3 Phase 3; docs/sports_news.md), needs db/042.
//
// Logged out: /sports is owner-only.
// Signed in as the owner's REAL account, on TEST rows only (db/042
// add_test_sports_story and add_test_move_proposal, the e2e marker):
//   * the chips keep their choice in the URL, through the tabs;
//   * a chip shows only its sport (Latest, with the marker's test stories);
//   * pillar separation: a Love on /sports writes pillar 'sports' only, never
//     'home', so it can train only the sports profile;
//   * a proposed move: Confirm on /settings/feed moves the test player to the
//     new team, and the decision is final.
// Test stories and test entities never reach a real profile, For you, Wags'
// context or the brief; the rows written here stay (append-only).

test.describe("logged out", () => {
  test("/sports asks for sign-in", async ({ page }) => {
    await page.goto("/sports?sport=golf");
    await expect(page).toHaveURL(/\/login\?next=%2Fsports/);
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
    await signIn(page, "/sports");
  });

  test.afterAll(async ({}, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("the chips keep their choice in the URL, through the tabs", async () => {
    await page.goto("/sports");
    await expect(page.getByTestId("sport-chip-all")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("sport-chip-golf").click();
    await expect(page).toHaveURL(/\/sports\?sport=golf$/);
    await expect(page.getByTestId("sport-chip-golf")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("sport-chip-all")).not.toHaveAttribute("aria-current", "page");
    await page.getByTestId("feed-tab-latest").click();
    await expect(page).toHaveURL(/\/sports\?sport=golf&feed=latest#stories$/);
    await expect(page.getByTestId("sport-chip-golf")).toHaveAttribute("aria-current", "page");
    // Changing the chip keeps the tab.
    await page.getByTestId("sport-chip-f1").click();
    await expect(page).toHaveURL(/\/sports\?sport=f1&feed=latest$/);
    await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
    // A reload keeps both.
    await page.reload();
    await expect(page.getByTestId("sport-chip-f1")).toHaveAttribute("aria-current", "page");
    // An unknown sport is All.
    await page.goto("/sports?sport=mlb");
    await expect(page.getByTestId("sport-chip-all")).toHaveAttribute("aria-current", "page");
  });

  test("a chip shows only its sport", async () => {
    const stamp = Date.now();
    const golf = `${P}golf chip ${stamp}`;
    const nfl = `${P}nfl chip ${stamp}`;
    await addTestSportsStory({ sport: "golf", title: golf, summary: "A golf test story.", url: `https://publisher.e2e.invalid/golf-${stamp}` });
    await addTestSportsStory({ sport: "nfl", title: nfl, summary: "An NFL test story.", url: `https://publisher.e2e.invalid/nfl-${stamp}` });
    const cards = () => page.getByTestId("feed-latest").getByTestId("feed-card");

    await page.goto("/sports?sport=golf&feed=latest");
    await expect(cards().filter({ hasText: golf })).toHaveCount(1);
    await expect(cards().filter({ hasText: nfl })).toHaveCount(0);

    await page.goto("/sports?sport=nfl&feed=latest");
    await expect(cards().filter({ hasText: nfl })).toHaveCount(1);
    await expect(cards().filter({ hasText: golf })).toHaveCount(0);

    await page.goto("/sports?feed=latest");
    await expect(cards().filter({ hasText: golf })).toHaveCount(1);
    await expect(cards().filter({ hasText: nfl })).toHaveCount(1);
  });

  test("a Love on Sports is a sports reaction, never Home's", async () => {
    const stamp = Date.now();
    const title = `${P}love on sports ${stamp}`;
    const id = await addTestSportsStory({ sport: "nba", title, summary: "A test story.", url: `https://publisher.e2e.invalid/love-${stamp}` });
    await page.goto("/sports?sport=nba&feed=latest");
    const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: title });
    await card.getByTestId("react-love").click();
    await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => storyReactions(id)).toEqual([{ pillar: "sports", reaction: "love" }]);
  });

  test("a confirmed move updates who the player plays for", async () => {
    const suffix = `e2e-${Date.now().toString(36)}`;
    const pid = await addTestMoveProposal(suffix);
    await page.goto("/settings/feed");
    const row = page.locator(`[data-testid="proposal"][data-proposal-id="${pid}"]`);
    await expect(row).toContainText(`Test Player ${suffix}`);
    await expect(row).toContainText(`Traded from Test Team A ${suffix} to Test Team B ${suffix}`);
    await expect(row).toContainText(`With Test Team A ${suffix} now`);
    await expect(row.getByTestId("test-tag")).toBeVisible();

    await row.getByTestId("proposal-confirm").click();
    await expect(page.getByTestId("proposals")).toHaveAttribute("aria-busy", "false");
    await expect(row).toHaveCount(0);
    const decided = page.getByTestId("proposal-decided").filter({ hasText: `Test Player ${suffix}` });
    await expect(decided).toContainText("confirmed");
    await expect(decided).toContainText(`with Test Team B ${suffix} now`);
  });
});
