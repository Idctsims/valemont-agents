import { expect, test, type BrowserContext, type Page } from "@playwright/test";

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

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await addTestStory({ pillar: "home", title: HEADLINE_ONLY, summary: null, url: URL_B });
  await addTestStory({ pillar: "home", title: WITH_SUMMARY, summary: SUMMARY, url: URL_A });
  context = await browser.newContext(testInfo.project.use);
  await useE2eMarker(context, testInfo.project.use.baseURL!);
  page = await context.newPage();
  await signIn(page, "/");
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

test("Home's Latest renders cards that open the publisher in a new tab", async () => {
  await page.goto("/");
  const latest = page.getByTestId("feed-latest");
  await expect(latest.getByRole("heading", { level: 2 })).toHaveText(/^Latest/);
  const card = latest.getByTestId("feed-card").filter({ hasText: WITH_SUMMARY });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("feed-meta")).toHaveText(/^Test source · (now|\d+m)$/);
  await expect(card.getByTestId("feed-test")).toHaveText("test");
  await expect(card.getByTestId("feed-summary")).toHaveText(SUMMARY);
  const link = card.getByTestId("feed-link");
  await expect(link).toHaveAttribute("href", URL_A);
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  // A tap on the card opens a new tab on the publisher, never Google News.
  const [popup] = await Promise.all([context.waitForEvent("page"), card.getByTestId("feed-summary").click()]);
  expect(popup.url()).toContain("publisher.e2e.invalid");
  await popup.close();
});

test("a story with no summary shows its headline alone", async () => {
  await page.goto("/");
  const card = page.getByTestId("feed-card").filter({ hasText: HEADLINE_ONLY });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("feed-summary")).toHaveCount(0);
  await expect(card.getByTestId("feed-outlets-toggle")).toHaveCount(0);
});

test("no card links to news.google.com", async () => {
  await page.goto("/");
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
  await signIn(p, "/");
  await expect(p.getByTestId("feed-latest")).toBeVisible();
  await expect(p.getByTestId("feed-test")).toHaveCount(0);
  await expect(p.getByText(WITH_SUMMARY)).toHaveCount(0);
  await expect(p.getByText(HEADLINE_ONLY)).toHaveCount(0);
  await plain.close();
});
