import { expect, test, type Page } from "@playwright/test";

import { addTestStory, addTestTitle, hasOwner, runPrefix, setTheme, signIn, useE2eMarker } from "./support/owner";

// Screenshot gate for Chat 4 Phase 4: the For you row, the seen-it card,
// Trailers For you (the REAL data, no marker), and a title page with "In the
// news" (a test title on Want and a test Home story naming it, with the
// marker: a real Want title may have no news yet). Both themes, phone
// 390x844 and desktop 1440. Written to e2e/screenshots/chat4-p4/
// (gitignored). Needs db/053 and the worker's first recommendations run.
//   pnpm test:e2e e2e/screens-screening-recs.spec.ts --project=phone --workers=1 --output=test-results/screens-recs-phone
//   pnpm test:e2e e2e/screens-screening-recs.spec.ts --project=desktop --workers=1 --output=test-results/screens-recs-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat4-p4";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("screening recommendation screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/screening");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/screening");
    await expect(page.getByTestId("for-you")).toBeVisible();
    await page.getByTestId("for-you").scrollIntoViewIfNeeded();
    await snap(page, "recs-for-you", phone, theme);

    await page.goto("/screening/seen");
    await expect(page.getByTestId("seen-pass").or(page.getByTestId("seen-none"))).toBeVisible();
    await snap(page, "recs-seen-card", phone, theme);

    await page.goto("/screening?tab=trailers");
    await expect(page.getByTestId("trailer-view-for_you")).toHaveAttribute("aria-current", "page");
    await snap(page, "recs-trailers-for-you", phone, theme);
  }

  // The title page with In the news: test rows, shown with the marker only.
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  const stamp = Date.now();
  const name = `${runPrefix()}gate title ${stamp}`;
  const t = await addTestTitle({ kind: "tv", name });
  await page.goto(`/screening/title/tv/${t.tmdbId}`);
  await page.getByTestId("shelf-want").click();
  await expect(page.getByTestId("shelf-buttons")).toHaveAttribute("aria-busy", "false");
  await addTestStory({ pillar: "home", title: `Inside ${name}: a season of power plays`,
                       summary: "The cast and the showrunner on what comes next.", url: `https://news.e2e.invalid/gate-${stamp}` });
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto(`/screening/title/tv/${t.tmdbId}`);
    await expect(page.getByTestId("title-news-item")).toHaveCount(1);
    await snap(page, "recs-title-news", phone, theme, true);
  }
});
