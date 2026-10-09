import { expect, test, type Page } from "@playwright/test";

import { addDays, localToday, monthStart, weekStart } from "../src/lib/goals/period";

import {
  SEEDED_WEEK,
  deleteSeededGoals,
  hasOwner,
  seedScreens,
  setTheme,
  signIn,
  snapshotGoals,
} from "./support/owner";

// Screenshot review for Chat 2 Phase 1: Home, every Goals tab, the More
// sheet, the composer while typing, and /design (the goal row states and
// week strips), at 390 px (phone project) and 1440 px (desktop project), in
// both themes. Written to e2e/screenshots/chat2-p1/ (gitignored).
//
// Runs against the owner's REAL account, so it is written to coexist with
// real goals:
//   - every count it asserts is relative: read before seeding, then
//     before + what the seed adds (SEEDED_WEEK);
//   - it deletes exactly the ids it created, nothing matched by pattern;
//   - afterwards it checks every goal the owner had is still there, unchanged.
// Owner-only. Run it ALONE and on one worker, so the two projects don't seed
// over each other or over goals.spec:
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
  const created: string[] = [];
  const owners = await snapshotGoals();
  try {
    // Before: whatever the owner's real week holds. Every assertion below
    // is this plus what the seed adds, so real goals never break the spec.
    await signIn(page, "/");
    const before = await readWeek(page);

    await seedScreens(created, thisWeek, addDays(thisWeek, -7), addDays(thisWeek, -14), monthStart(today));
    await page.reload();
    const week = page.getByTestId("goals-weekly");
    const done = before.done + SEEDED_WEEK.done;
    const held = before.held + SEEDED_WEEK.open + SEEDED_WEEK.done;
    await expect(week.getByTestId("goals-fraction")).toHaveText(`${done} done · ${held} of 10`);
    await expect(week.locator('[data-segment="done"]')).toHaveCount(done);
    await expect(week.locator('[data-segment="open"]')).toHaveCount(held - done);
    await expect(week.locator('[data-segment="empty"]')).toHaveCount(Math.max(0, 10 - held));
    await expect(week.locator("[data-over]")).toHaveCount(Math.max(0, held - 10));
    await expect(week.getByTestId("cap-warning")).toHaveCount(held > 10 ? 1 : 0);
    await expect(
      week.getByRole("button", { name: `Moved on · ${before.folded + SEEDED_WEEK.folded}` }),
    ).toHaveAttribute("aria-expanded", "false");
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
        await page.waitForTimeout(300); // let the chevron finish its 180 ms turn
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
    await deleteSeededGoals(created);
  }

  // Nothing the owner had was touched: every goal from before is still there,
  // field for field. A new row is acceptable only as a rollover carry of one
  // of the owner's own goals (ensureRollover runs on every page load).
  const after = await snapshotGoals();
  for (const [id, fields] of owners) expect(after.get(id), `owner goal ${id} changed`).toBe(fields);
  for (const [id, fields] of after) {
    if (owners.has(id)) continue;
    const from = (JSON.parse(fields) as { carried_from: string | null }).carried_from;
    expect(from && owners.has(from), `goal ${id} is new and not a rollover of an owner goal`).toBe(true);
  }
});

/** The owner's week as Home shows it: done, slots held, folded away. */
async function readWeek(page: Page): Promise<{ done: number; held: number; folded: number }> {
  const week = page.getByTestId("goals-weekly");
  await expect(week).toHaveAttribute("aria-busy", "false");
  const text = (await week.getByTestId("goals-fraction").textContent()) ?? "";
  const m = text.match(/^(\d+) done · (\d+) of 10$/);
  expect(m, `unexpected count label ${JSON.stringify(text)}`).not.toBeNull();
  const fold = week.getByRole("button", { name: /^Moved on · \d+$/ });
  const folded = (await fold.count()) ? Number(((await fold.textContent()) ?? "").match(/(\d+)\s*$/)![1]) : 0;
  return { done: Number(m![1]), held: Number(m![2]), folded };
}
