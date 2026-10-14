import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 4 Phase 1: /tech (All) and /tech?category=
// pricing_credits, plus the tech source health list on /settings/feed, in
// both themes. Phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat4-p1/ (gitignored). Needs db/045 and the worker's
// first tech ingest.
//   pnpm test:e2e e2e/screens-tech.spec.ts --workers=1 --output=test-results/screens-tech
// The feed is the REAL feed (no test marker).

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat4-p1";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("tech screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(240_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/tech");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/tech");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tech Hub");
    await expect(page.getByTestId("category-chip-all")).toHaveAttribute("aria-current", "page");
    await snap(page, "tech-all", phone, theme);

    await page.goto("/tech?category=pricing_credits");
    await expect(page.getByTestId("category-chip-pricing_credits")).toHaveAttribute("aria-current", "page");
    await snap(page, "tech-pricing-credits", phone, theme);

    await page.goto("/settings/feed");
    const list = page.getByTestId("tech-sources");
    await list.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollBy(0, -24));
    await snap(page, "settings-feed-tech-sources", phone, theme, false);
  }
});
