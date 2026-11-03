import { expect, test, type Page } from "@playwright/test";

import { addTestTitle, addTestTitleHookCards, hasOwner, runPrefix, setTheme, signIn, useE2eMarker } from "./support/owner";

// Screenshot gate for Chat 5 Phase 3, the Screening Room hook: a finished
// title's page while it drafts ("Drafting…"), the same page with its cards
// (Checked, Unverified with its reason, the Power Word, one saved), and My
// Arsenal with "From the Screening Room" on. All on TEST rows with the
// marker; the cards come from add_test_title_hook_cards, never the model.
// Both themes, phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat5-p3/ (gitignored). Needs db/062.
//   pnpm test:e2e e2e/screens-arsenal-hook.spec.ts --project=phone --workers=1 --output=test-results/screens-arsenal-hook-phone
//   pnpm test:e2e e2e/screens-arsenal-hook.spec.ts --project=desktop --workers=1 --output=test-results/screens-arsenal-hook-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat5-p3";
const THEMES = ["night", "day"] as const;

// The fixed dock and the sticky phone header, hidden for the capture only
// (a full-page capture paints them mid-page); the app is unchanged.
const HIDE_CHROME = 'nav[aria-label="Dock"], header.sticky { visibility: hidden !important; }';

async function snap(page: Page, name: string, phone: boolean, theme: string) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${phone ? "390" : "1440"}-${theme}.png`, fullPage: true, style: HIDE_CHROME });
}

async function finish(page: Page, tmdbId: number) {
  await page.goto(`/screening/title/tv/${tmdbId}`);
  await page.getByTestId("shelf-finished").click();
  await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
  await page.reload();
  await expect(page.getByTestId("title-arsenal")).toBeVisible();
}

test("arsenal hook screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  await signIn(page, "/screening");
  const P = runPrefix();
  const stamp = Date.now();

  for (const theme of THEMES) {
    await setTheme(page, theme);

    const drafting = await addTestTitle({ kind: "tv", name: `${P}Drafting show ${theme} ${stamp}` });
    await finish(page, drafting.tmdbId);
    await expect(page.getByTestId("title-arsenal-drafting")).toBeVisible();
    await snap(page, "title-drafting", phone, theme);

    const drafted = await addTestTitle({ kind: "tv", name: `${P}Finished show ${theme} ${stamp}` });
    await finish(page, drafted.tmdbId);
    await addTestTitleHookCards(drafted.id, 3);
    await page.reload();
    const cards = page.getByTestId("title-arsenal-card");
    await expect(cards).toHaveCount(3);
    await cards.nth(1).getByTestId("card-save").click();
    await expect(cards.nth(1).getByTestId("card-save")).toHaveAttribute("aria-pressed", "true");
    await page.getByTestId("title-arsenal").scrollIntoViewIfNeeded();
    await snap(page, "title-cards", phone, theme);

    await page.goto("/arsenal/saved");
    await page.getByTestId("saved-filter-from-screening").click();
    await expect(page.getByTestId("saved-item").first()).toBeVisible();
    await snap(page, "my-arsenal-from-screening", phone, theme);
  }
});
