import { test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot review for Chat 2 Phase 1: Home, every Goals tab and the More
// sheet, at 390 px (phone project) and 1440 px (desktop project), in both
// themes. Written to e2e/screenshots/chat2-p1/ (gitignored). Owner-only.
// Run alone: pnpm test:e2e screens

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p1";
const PAGES = [
  ["home", "/"],
  ["goals-week", "/goals"],
  ["goals-month", "/goals?view=month"],
  ["goals-long-term", "/goals?view=long-term"],
  ["goals-history", "/goals?view=history"],
] as const;

async function shoot(page: Page, name: string, width: string, theme: string, fullPage = true) {
  await page.screenshot({ path: `${DIR}/${name}-${width}-${theme}.png`, fullPage });
}

test("screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  const width = phone ? "390" : "1440";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/");

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    for (const [name, path] of PAGES) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await shoot(page, name, width, theme);
    }
    if (phone) {
      await page.goto("/goals");
      await page.getByRole("navigation", { name: "Dock" }).getByRole("button", { name: "More" }).click();
      await shoot(page, "more-sheet", width, theme, false);
      // While typing: the area chips over the pinned composer.
      await page.keyboard.press("Escape");
      await page.getByRole("textbox", { name: "Add a goal for this week" }).fill("Draft the Valemont Grow deck");
      await shoot(page, "composer-typing", width, theme, false);
    }
  }
});
