import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot review for Chat 3 Phase 1: Home "Latest" and /settings/sources,
// in both themes, on the REAL feed (no test marker: these are the owner's
// actual stories, so the shots show real summaries, real "+N outlets" and
// the real filtered list). Phone 390x844: Latest scrolled into view, a
// multi-outlet card opened, /settings/sources top and the Filtered fold
// open. Desktop 1440: both pages full. Written to e2e/screenshots/chat3-p1/
// (gitignored).
//   pnpm test:e2e e2e/screens-feed.spec.ts --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat3-p1";

async function snap(page: Page, name: string, phone: boolean, theme: string) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage: !phone });
}

test("feed screens", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/");

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.goto("/#latest");
    await expect(page.getByTestId("feed-latest")).toBeVisible();
    await page.getByTestId("feed-latest").scrollIntoViewIfNeeded();
    await snap(page, "home-latest", phone, theme);

    const multi = page.getByTestId("feed-outlets-toggle").first();
    if (await multi.count()) {
      await multi.scrollIntoViewIfNeeded();
      await multi.click();
      await page.evaluate(() => window.scrollBy(0, -120));
      await snap(page, "home-latest-outlets", phone, theme);
    }

    await page.goto("/settings/sources");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sources");
    await snap(page, "sources", phone, theme);
    await page.getByTestId("sources-filtered").getByRole("button").click();
    await page.getByTestId("sources-filtered").scrollIntoViewIfNeeded();
    await snap(page, "sources-filtered", phone, theme);
  }
});
