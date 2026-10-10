import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { addTestBrief, chicagoDay, hasOwner, runPrefix, signIn, useE2eMarker } from "./support/owner";

// The Morning Brief, signed in as the owner's REAL account. Two TEST briefs
// (today and yesterday) are seeded through the e2e marker path (db/030
// add_test_brief): is_test, so the real views never show them, and a marked
// request does. The last test opens Home WITHOUT the marker and checks the
// test brief is invisible there. Briefs are append-only: the test rows stay,
// invisible, like capital's test entries. Wags runs on the mock model.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

const P = runPrefix();
const TODAY = chicagoDay();
const YESTERDAY = chicagoDay(-1);
const HEADLINE = `${P}permits decide the week`;
const OLD_HEADLINE = `${P}yesterday's brief`;

let context: BrowserContext;
let page: Page;

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await addTestBrief(YESTERDAY, { headline: OLD_HEADLINE, one_move: "Old move.", sections: [] });
  await addTestBrief(TODAY, {
    headline: HEADLINE,
    one_move: `${P}file the DEP application before noon.`,
    sections: [
      { key: "goals", title: "Goals", lines: ["Goals: 2 of 8 done."] },
      { key: "ventures", title: "Ventures", lines: ["Sail Beach Club: DEP filing overdue.", "Sail Beach Club: lender call due today."] },
    ],
  });
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

test("Home: the brief sits on top, above TODAY, and opens inline", async () => {
  await page.goto("/");
  const card = page.getByTestId("brief-card");
  await expect(card.getByTestId("brief-headline")).toHaveText(HEADLINE);
  await expect(card.getByTestId("brief-label")).toContainText(/^Morning brief · \d{1,2}:\d{2} [AP]M/i);
  await expect(card.getByTestId("brief-plain")).toHaveText("plain mode");
  await expect(card.getByTestId("brief-test")).toHaveText("test");
  await expect(card.getByTestId("brief-one-move")).toContainText(`${P}file the DEP application before noon.`);
  // Above TODAY and This week: first after the date.
  const order = await page.evaluate(() => {
    const at = (sel: string) => document.querySelector(sel)?.getBoundingClientRect().top ?? Infinity;
    return { brief: at('[data-testid="brief-card"]'), week: at('[data-testid="goals-weekly"]'), today: at('[data-testid="venture-today"]') };
  });
  expect(order.brief).toBeLessThan(order.week);
  expect(order.brief).toBeLessThan(order.today);
  await expect(card.getByTestId("brief-section")).toHaveCount(0);
  await card.getByRole("button", { name: "Read the brief" }).click();
  await expect(card.getByTestId("brief-section")).toHaveCount(2);
  await expect(card.getByRole("button", { name: "Hide the brief" })).toHaveAttribute("aria-expanded", "true");
});

test("/brief renders today's brief: headline, sections, one move last", async () => {
  await page.goto("/brief");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(HEADLINE);
  const sections = page.getByTestId("brief-section");
  await expect(sections).toHaveCount(2);
  await expect(sections.nth(1)).toHaveAttribute("data-key", "ventures");
  await expect(sections.nth(1).getByRole("listitem")).toHaveCount(2);
  const order = await page.evaluate(() => {
    const at = (sel: string) => document.querySelector(sel)?.getBoundingClientRect().top ?? 0;
    return { lastSection: at('[data-testid="brief-section"]:last-of-type'), move: at('[data-testid="brief-one-move"]') };
  });
  expect(order.move).toBeGreaterThan(order.lastSection);
});

test("history lists past dates in a fold and opens one by date", async () => {
  await page.goto("/brief");
  const fold = page.getByTestId("brief-history");
  await fold.getByRole("button", { name: /^Past briefs · \d+$/ }).click();
  await fold.getByRole("link").filter({ hasText: OLD_HEADLINE }).click();
  await expect(page).toHaveURL(new RegExp(`/brief/${YESTERDAY}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(OLD_HEADLINE);
  await page.goto(`/brief/${TODAY}`);
  await expect(page).toHaveURL(/\/brief$/);
});

test("Ask Wags about this opens the sheet with Context · Brief", async () => {
  await page.goto("/");
  await page.getByTestId("brief-card").getByTestId("brief-ask-wags").click();
  const sheet = page.getByTestId("wags-sheet");
  await expect(sheet.getByTestId("wags-context-chip")).toHaveText("Context · Brief");
  await sheet.getByRole("button", { name: "Close", exact: true }).click();
  // Opened from the dock afterwards, Home is Home again.
  await page.getByTestId("dock-wags").click();
  await expect(page.getByTestId("wags-sheet").getByTestId("wags-context-chip")).toHaveText("Context · Home");
  await page.getByTestId("wags-sheet").getByRole("button", { name: "Close", exact: true }).click();
});

test("the real view never shows a test brief", async ({ browser }, testInfo) => {
  const plain = await browser.newContext(testInfo.project.use);
  const p = await plain.newPage();
  await signIn(p, "/");
  await expect(p.getByTestId("brief-test")).toHaveCount(0);
  await expect(p.getByText(HEADLINE)).toHaveCount(0);
  await p.goto("/brief");
  await expect(p.getByText(HEADLINE)).toHaveCount(0);
  await expect(p.getByText(OLD_HEADLINE)).toHaveCount(0);
  await plain.close();
});
