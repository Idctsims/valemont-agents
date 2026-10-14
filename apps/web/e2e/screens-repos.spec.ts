import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot gate for Chat 4 Phase 2: /tech?category=repos (For you and
// Latest), in both themes. Phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat4-p2/ (gitignored). Needs db/049 and the worker's
// first tech_repos run (the cards are the REAL feed: no test marker).
//   pnpm test:e2e e2e/screens-repos.spec.ts --workers=1 --output=test-results/screens-repos

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat4-p2";
const THEMES = ["night", "day"] as const;

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

test("repos screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(240_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/tech?category=repos");
  for (const theme of THEMES) {
    await setTheme(page, theme);
    await page.goto("/tech?category=repos");
    await expect(page.getByTestId("category-chip-repos")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("repo-facts").first().or(page.getByTestId("feed-empty"))).toBeVisible();
    await snap(page, "tech-repos", phone, theme);

    await page.goto("/tech?category=repos&feed=latest");
    await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
    await snap(page, "tech-repos-latest", phone, theme);
  }
});
