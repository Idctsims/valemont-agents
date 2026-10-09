import { test, type Page } from "@playwright/test";

import { addDays, localToday, monthStart, weekStart } from "../src/lib/goals/period";

import { deleteE2eGoals, hasOwner, seedScreens, setTheme, signIn } from "./support/owner";

// Screenshot review for Chat 2 Phase 1: Home, every Goals tab, the More
// sheet, the composer while typing, and /design (the goal row states and
// week strips), at 390 px (phone project) and 1440 px (desktop project), in
// both themes. Written to e2e/screenshots/chat2-p1/ (gitignored).
//
// Seeds a realistic set of goals first (open, done, carried twice, dropped,
// moved on, a past week, month and long term), marked notes = 'e2e-seed',
// and deletes them afterwards. Owner-only. Run it ALONE and on one worker,
// so the two projects don't seed over each other or over goals.spec:
//   pnpm test:e2e screens --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p1";
const PAGES = [
  ["home", "/"],
  ["goals-week", "/goals"],
  ["goals-month", "/goals?view=month"],
  ["goals-long-term", "/goals?view=long-term"],
  ["goals-history", "/goals?view=history"],
  ["design", "/design"],
] as const;

async function shoot(page: Page, name: string, width: string, theme: string, fullPage = true) {
  await page.screenshot({ path: `${DIR}/${name}-${width}-${theme}.png`, fullPage });
}

test("screens", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const phone = testInfo.project.name === "phone";
  const width = phone ? "390" : "1440";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });

  const today = localToday();
  const thisWeek = weekStart(today);
  await deleteE2eGoals({ seed: true });
  await seedScreens(thisWeek, addDays(thisWeek, -7), addDays(thisWeek, -14), monthStart(today));
  try {
    await signIn(page, "/");
    for (const theme of ["night", "day"] as const) {
      await setTheme(page, theme);
      for (const [name, path] of PAGES) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        if (name === "goals-history") {
          // Open the most recent week so its rows are in the shot.
          await page.locator("[data-testid=goals-history] summary").first().click();
        }
        await shoot(page, name, width, theme);
      }
      if (phone) {
        // Viewport shots (what the phone shows, not fullPage), at load and
        // scrolled to the bottom: does the dock or the composer cover the
        // last row?
        for (const [name, path] of [["home", "/"], ["goals-week", "/goals"]] as const) {
          await page.goto(path);
          await page.waitForLoadState("networkidle");
          await shoot(page, `${name}-viewport-top`, width, theme, false);
          await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
          await page.waitForTimeout(250);
          await shoot(page, `${name}-viewport-bottom`, width, theme, false);
        }
        // The fold, opened.
        await page.getByTestId("goals-weekly").getByRole("button", { name: /^Moved on/ }).click();
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await shoot(page, "goals-week-fold-open", width, theme, false);

        await page.goto("/goals");
        await page.getByRole("navigation", { name: "Dock" }).getByRole("button", { name: "More" }).click();
        await shoot(page, "more-sheet", width, theme, false);
        await page.keyboard.press("Escape");
        await page.getByRole("textbox", { name: "Add a goal for this week" }).fill("Draft the Valemont Grow deck");
        await shoot(page, "composer-typing", width, theme, false);
      }
    }
  } finally {
    await deleteE2eGoals({ seed: true });
  }
});
