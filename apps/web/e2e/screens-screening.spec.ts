import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 4 Phase 3: /screening (the shelves), a title page
// (City of God, movie 598) and the Trailers tab, both themes. Phone 390x844
// and desktop 1440. Written to e2e/screenshots/chat4-p3/ (gitignored). Needs
// db/051, TMDB_READ_TOKEN in apps/web/.env.local, and the worker's first
// trailer runs. The REAL feed (no test marker).
//   pnpm test:e2e e2e/screens-screening.spec.ts --project=phone --workers=1 --output=test-results/screens-screening-phone
//   pnpm test:e2e e2e/screens-screening.spec.ts --project=desktop --workers=1 --output=test-results/screens-screening-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat4-p3";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("screening screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(240_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/screening");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/screening");
    await expect(page.getByTestId("screening-tab-shelves")).toHaveAttribute("aria-current", "page");
    await snap(page, "screening-shelves", phone, theme);

    await page.goto("/screening/title/movie/598");
    await expect(page.getByTestId("title-name")).toBeVisible();
    await snap(page, "screening-title", phone, theme);

    await page.goto("/screening?tab=trailers");
    await expect(page.getByTestId("screening-tab-trailers")).toHaveAttribute("aria-current", "page");
    await snap(page, "screening-trailers", phone, theme);
  }
});
