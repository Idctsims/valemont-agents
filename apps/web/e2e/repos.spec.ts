import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { RUN_ID, addTestRepo, addTestTechStory, hasOwner, runPrefix, signIn, storyReactions, useE2eMarker } from "./support/owner";

// GitHub repos in Tech Hub (Chat 4 Phase 2; db/049, docs/tech_hub.md §7),
// needs db/049.
//
// Logged out: the Repos chip is owner-only like the rest of /tech.
// Signed in as the owner's REAL account, on TEST rows only (db/049
// add_test_repo: is_test repo, story and surfacing from `_e2e-repos`, the e2e
// marker):
//   * the Repos chip shows only repos: a repo card, never another category's;
//   * the card: owner/name, summary, stars, stars_7d with its label (an
//     estimate says "trending est."; a measurement "7d"), language, at most 3
//     topics, the link;
//   * a Love on a repo writes pillar 'tech', never Home's or Sports';
//   * a test repo is invisible without the marker.
// Rows written here stay (append-only); each is named with this run's id.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/repos.spec.ts --project=phone --output=test-results/repos

/** A repo name: owner/name characters only ([A-Za-z0-9_.-]), unique per run. */
const repoName = (what: string) => `e2e-${RUN_ID}/${what}-${Date.now()}`;

test.describe("logged out", () => {
  test("the Repos chip asks for sign-in", async ({ page }) => {
    await page.goto("/tech?category=repos");
    await expect(page).toHaveURL(/\/login\?next=%2Ftech/);
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

  test("the Repos chip shows only repos", async () => {
    const name = repoName("only");
    const tooling = `${P}not a repo ${Date.now()}`;
    await addTestRepo({ fullName: name, summary: "A repo test story.", stars: 1234, stars7d: 300, stars7dSource: "trending_weekly" });
    await addTestTechStory({ category: "tooling", title: tooling, summary: "A tooling test story.", url: `https://publisher.e2e.invalid/tooling-${Date.now()}` });

    await page.goto("/tech");
    await page.getByTestId("category-chip-repos").click();
    await expect(page).toHaveURL(/\/tech\?category=repos$/);
    await expect(page.getByTestId("category-chip-repos")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("feed-tab-latest").click();
    await expect(page).toHaveURL(/\/tech\?category=repos&feed=latest#stories$/);

    const cards = page.getByTestId("feed-latest").getByTestId("feed-card");
    await expect(cards.filter({ hasText: name })).toHaveCount(1);
    await expect(cards.filter({ hasText: tooling })).toHaveCount(0);
    // Every card under Repos is a repo card.
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    await expect(page.getByTestId("feed-latest").getByTestId("repo-facts")).toHaveCount(n);
    // The tooling story is still on Tech, under its own chip.
    await page.goto("/tech?category=tooling&feed=latest");
    await expect(page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: tooling })).toHaveCount(1);
  });

  test("a repo card: stars, a labelled stars_7d, language, 3 topics at most, the link", async () => {
    const estimated = repoName("estimated");
    const measured = repoName("measured");
    await addTestRepo({ fullName: estimated, summary: "Estimated stars test.", stars: 238274, stars7d: 1204, stars7dSource: "trending_weekly" });
    await addTestRepo({ fullName: measured, summary: null, stars: 50, stars7d: 12, stars7dSource: "measured" });
    await page.goto("/tech?category=repos&feed=latest");
    const card = (n: string) => page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: n });

    const est = card(estimated);
    await expect(est.getByTestId("feed-link")).toHaveText(estimated);
    await expect(est.getByTestId("feed-link")).toHaveAttribute("href", `https://e2e.invalid/repos/${estimated}`);
    await expect(est.getByTestId("feed-summary")).toHaveText("Estimated stars test.");
    await expect(est.getByTestId("repo-stars")).toContainText("238,274");
    await expect(est.getByTestId("repo-stars-7d")).toHaveText("+1,204 trending est.");
    await expect(est.getByTestId("repo-stars-7d")).toHaveAttribute("data-estimate", "true");
    await expect(est.getByTestId("repo-language")).toHaveText("TypeScript");
    await expect(est.getByTestId("repo-topic")).toHaveText(["e2e-test", "agents"]);
    expect(await est.getByTestId("repo-topic").count()).toBeLessThanOrEqual(3);
    await expect(est.getByTestId("feed-meta")).toContainText("Test repos source");

    const real = card(measured);
    await expect(real.getByTestId("repo-stars-7d")).toHaveText("+12 7d");
    await expect(real.getByTestId("repo-stars-7d")).not.toHaveAttribute("data-estimate", /.*/);
    await expect(real.getByTestId("feed-summary")).toHaveCount(0);
  });

  test("a Love on a repo is a tech reaction, never Home's or Sports'", async () => {
    const name = repoName("love");
    const id = await addTestRepo({ fullName: name, summary: "Love test.", stars: 10, stars7d: 2, stars7dSource: "since_created" });
    await page.goto("/tech?category=repos&feed=latest");
    const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: name });
    await card.getByTestId("react-love").click();
    await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
    await expect.poll(() => storyReactions(id)).toEqual([{ pillar: "tech", reaction: "love" }]);
  });

  test("a test repo is invisible without the marker", async ({}, testInfo) => {
    const baseURL = testInfo.project.use.baseURL!;
    const name = repoName("hidden");
    await addTestRepo({ fullName: name, summary: "Hidden test.", stars: 10, stars7d: null, stars7dSource: null });
    await page.goto("/tech?category=repos&feed=latest");
    await expect(page.getByText(name)).toHaveCount(1);
    try {
      // Same signed-in context, marker off (a second sign-in trips Supabase Auth's limit).
      await context.unroute(`${baseURL}/**`);
      for (const url of ["/tech?category=repos&feed=latest", "/tech?category=repos", "/tech?feed=latest", "/tech"]) {
        await page.goto(url);
        await expect(page.getByTestId("feed-card").first().or(page.getByTestId("feed-empty"))).toBeVisible();
        await expect(page.getByText(name), url).toHaveCount(0);
      }
    } finally {
      await useE2eMarker(context, baseURL);
    }
  });
});
