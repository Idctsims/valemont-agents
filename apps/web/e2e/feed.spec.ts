import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { addTestStory, hasOwner, runPrefix, signIn, useE2eMarker } from "./support/owner";

// The feed, signed in as the owner's REAL account. Two TEST stories are
// seeded through the e2e marker path (db/032 add_test_story): is_test, so the
// real feed never shows them, and a marked request does. Their links are
// .invalid publisher URLs, never news.google.com. Stories are kept like
// briefs: the test rows stay, invisible.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

const P = runPrefix();
const WITH_SUMMARY = `${P}Ross and Soffer scrap the airport hotel`;
const HEADLINE_ONLY = `${P}Texans trade for Harrison`;
const SUMMARY = "The FAA grounded the plan, so the developers walked away from the site.";
const URL_A = `https://publisher.e2e.invalid/${Date.now()}-a`;
const URL_B = `https://publisher.e2e.invalid/${Date.now()}-b`;
// Test stories reach Latest only (For you never shows one: db/039).
const LATEST = "/?feed=latest";

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await addTestStory({ pillar: "home", title: HEADLINE_ONLY, summary: null, url: URL_B });
  await addTestStory({ pillar: "home", title: WITH_SUMMARY, summary: SUMMARY, url: URL_A });
  context = await browser.newContext(testInfo.project.use);
  await useE2eMarker(context, testInfo.project.use.baseURL!);
  // A .invalid host cannot resolve: the new tab would land on
  // chrome-error://chromewebdata/ and lose its URL. Serve a stub instead, so
  // the tab keeps the publisher URL and nothing leaves the machine.
  await context.route("https://publisher.e2e.invalid/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<title>publisher</title>" }),
  );
  page = await context.newPage();
  await signIn(page, LATEST);
});

/**
 * Tap the centre of `target` the way a finger does, by coordinates, and
 * return the URL of the tab it opens. Not locator.click(): Playwright refuses
 * to click an element that another element covers, and the headline link's
 * overlay covers the whole row on purpose ("... intercepts pointer events").
 */
async function tapOpens(target: Locator): Promise<string> {
  const box = await target.boundingBox();
  if (!box) throw new Error("target not on screen");
  const [popup] = await Promise.all([
    context.waitForEvent("page"),
    page.mouse.click(box.x + box.width / 2, box.y + box.height / 2),
  ]);
  await popup.waitForLoadState("domcontentloaded");
  const url = popup.url();
  await popup.close();
  return url;
}

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

test("Home's Latest renders cards that open the publisher in a new tab", async () => {
  await page.goto(LATEST);
  await expect(page.getByTestId("feed").getByRole("heading", { level: 2 })).toHaveText(/^Latest/);
  await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
  const latest = page.getByTestId("feed-latest");
  const card = latest.getByTestId("feed-card").filter({ hasText: WITH_SUMMARY });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("feed-meta")).toHaveText(/^Test source · (now|\d+m)$/);
  await expect(card.getByTestId("feed-test")).toHaveText("test");
  await expect(card.getByTestId("feed-summary")).toHaveText(SUMMARY);
  const link = card.getByTestId("feed-link");
  await expect(link).toHaveAttribute("href", URL_A);
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

test("the whole row opens the publisher: the summary and the source/time line too", async () => {
  await page.goto(LATEST);
  const card = page.getByTestId("feed-card").filter({ hasText: WITH_SUMMARY });
  await card.scrollIntoViewIfNeeded();
  for (const part of ["feed-summary", "feed-meta"] as const) {
    // What is under the centre of that part is the headline link's overlay.
    const box = (await card.getByTestId(part).boundingBox())!;
    const under = await page.evaluate(
      ([x, y]) => document.elementFromPoint(x, y)?.closest("a")?.getAttribute("href") ?? null,
      [box.x + box.width / 2, box.y + box.height / 2],
    );
    expect(under, part).toBe(URL_A);
    expect(await tapOpens(card.getByTestId(part)), part).toBe(URL_A);
  }
  expect(await tapOpens(card.getByTestId("feed-link"))).toBe(URL_A);
});

test("the +N outlets toggle opens its fold, never the publisher", async ({ browser }, testInfo) => {
  // Test stories have one outlet each, so a REAL multi-outlet story is used,
  // on the REAL feed: no marker. (With the marker, page one is ordered by
  // first-seen time with test stories first, which is why this skipped on
  // 2026-10-11 although the real page one had "+1 outlet".)
  const plain = await browser.newContext(testInfo.project.use);
  const p = await plain.newPage();
  await signIn(p, LATEST);
  const toggle = p.getByTestId("feed-outlets-toggle").first();
  test.skip((await toggle.count()) === 0, "no multi-outlet story on the real first page right now");
  await toggle.scrollIntoViewIfNeeded();
  const opened: string[] = [];
  plain.on("page", (np) => opened.push(np.url()));
  const box = (await toggle.boundingBox())!;
  await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(p.getByTestId("feed-outlets").first()).toBeVisible();
  await p.waitForTimeout(1500);
  expect(opened).toEqual([]);
  await plain.close();
});

test("a story with no summary shows its headline alone", async () => {
  await page.goto(LATEST);
  const card = page.getByTestId("feed-card").filter({ hasText: HEADLINE_ONLY });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("feed-summary")).toHaveCount(0);
  await expect(card.getByTestId("feed-outlets-toggle")).toHaveCount(0);
});

test("no card links to news.google.com", async () => {
  await page.goto(LATEST);
  const hrefs = await page.getByTestId("feed-link").evaluateAll((as) => as.map((a) => a.getAttribute("href") ?? ""));
  expect(hrefs.length).toBeGreaterThan(0);
  for (const h of hrefs) expect(h).not.toContain("news.google.com");
});

test("/settings/sources lists the sources and the filtered fold", async () => {
  await page.goto("/settings/sources");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sources");
  await expect(page.getByTestId("sources-summary")).toHaveText(/^\d+ of \d+ active sources OK$/);
  await expect(page.getByTestId("source-row").first()).toBeVisible();
  // The test source never appears.
  await expect(page.locator('[data-slug="_e2e"]')).toHaveCount(0);
  const fold = page.getByTestId("sources-filtered");
  await fold.getByRole("button", { name: /^Filtered today · \d+$/ }).click();
  await expect(fold.getByRole("button")).toHaveAttribute("aria-expanded", "true");
});

test("the real feed never shows a test story", async ({ browser }, testInfo) => {
  const plain = await browser.newContext(testInfo.project.use);
  const p = await plain.newPage();
  await signIn(p, LATEST);
  for (const path of [LATEST, "/"]) {
    await p.goto(path);
    await expect(p.getByTestId(path === "/" ? "feed-for-you" : "feed-latest")).toBeVisible();
    await expect(p.getByTestId("feed-test")).toHaveCount(0);
  }
  await expect(p.getByText(WITH_SUMMARY)).toHaveCount(0);
  await expect(p.getByText(HEADLINE_ONLY)).toHaveCount(0);
  await plain.close();
});
