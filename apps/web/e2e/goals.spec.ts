import { expect, test, type Locator, type Page } from "@playwright/test";

import { deleteE2eGoals, hasOwner, runPrefix, signIn, snapshotGoals } from "./support/owner";

// Goals and navigation, signed in as the owner's REAL account. Skipped unless
// the owner's credentials are in the shell session (see support/owner.ts).
// Every goal these specs add is titled "e2e <run id> …" and deleted
// afterwards; every count is read before it is asserted against; and the
// owner's own goals are checked unchanged at the end.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

let owners = new Map<string, string>();

test.beforeAll(async () => {
  owners = await snapshotGoals();
});

test.afterAll(async () => {
  // Only this worker's goals: the other project may still be mid-test.
  await deleteE2eGoals({ titlePrefix: runPrefix() });
  const after = await snapshotGoals();
  for (const [id, fields] of owners) expect(after.get(id), `owner goal ${id} changed`).toBe(fields);
});

const week = (page: Page) => page.getByTestId("goals-weekly");
const row = (page: Page, title: string): Locator =>
  week(page).getByTestId("goal-row").filter({ hasText: title });

async function addGoal(page: Page, title: string) {
  const input = week(page).getByRole("textbox", { name: "Add a goal for this week" });
  await input.fill(title);
  await input.press("Enter");
  await expect(row(page, title)).toBeVisible();
}

test("goals CRUD on the phone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
  await signIn(page, "/goals");
  const title = `${runPrefix()}crud`;

  await addGoal(page, title);
  // The optimistic row is replaced by the server's: it stops saying "saving".
  await expect(row(page, title)).not.toContainText("saving");

  const ring = row(page, title).getByRole("checkbox");
  await ring.click();
  await expect(row(page, title)).toHaveAttribute("data-state", "done");
  await ring.click();
  await expect(row(page, title)).toHaveAttribute("data-state", "open");

  // Edit through the row menu.
  await row(page, title).getByRole("button", { name: `Actions for ${title}` }).click();
  await page.getByRole("menuitem", { name: "Edit" }).click();
  const edited = `${title} edited`;
  // Not row(page, title): while editing, the title is an input's value, and
  // hasText does not match input values, so that locator matches nothing.
  await week(page).getByRole("textbox", { name: "Goal title" }).fill(edited);
  await page.getByRole("button", { name: "health" }).click();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(row(page, edited)).toContainText("health");

  // Drop and restore through the menu (the tap path for the left swipe).
  await row(page, edited).getByRole("button", { name: `Actions for ${edited}` }).click();
  await page.getByRole("menuitem", { name: "Drop" }).click();
  // Dropped goals fold away under "Moved on · N", closed until tapped.
  const fold = week(page).getByTestId("moved-on");
  await expect(fold.getByRole("button", { name: /^Moved on · \d+$/ })).toHaveAttribute("aria-expanded", "false");
  await expect(row(page, edited)).toHaveCount(0);
  await fold.getByRole("button", { name: /^Moved on/ }).click();
  await expect(row(page, edited)).toHaveAttribute("data-state", "dropped");
  await row(page, edited).getByRole("button", { name: `Actions for ${edited}` }).click();
  await page.getByRole("menuitem", { name: "Restore" }).click();
  await expect(row(page, edited)).toHaveAttribute("data-state", "open");

  // Swipe right completes.
  const box = (await row(page, edited).boundingBox())!;
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + 80, y);
  await page.mouse.down();
  await page.mouse.move(box.x + 140, y, { steps: 4 });
  await page.mouse.move(box.x + 200, y, { steps: 4 });
  await page.mouse.up();
  await expect(row(page, edited)).toHaveAttribute("data-state", "done");

  // It survives a reload: the server has it, not just the optimistic list.
  // Actions are queued one at a time; reloading before the queue drains
  // aborts the rest (the 2026-10-09 failure: Restore and the swipe were lost).
  await expect(week(page)).toHaveAttribute("aria-busy", "false");
  await page.reload();
  await expect(row(page, edited)).toHaveAttribute("data-state", "done");
});

test("a server-side refusal reads as its message, not a React error", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
  await signIn(page, "/goals");
  const title = `${runPrefix()}gone`;
  await addGoal(page, title);
  await expect(row(page, title)).not.toContainText("saving");

  // Delete the goal behind the page's back. The next tap reaches the server,
  // which finds no row and refuses. Not a network abort: the POST succeeds.
  // A thrown refusal showed "Minified React error" (number 441) here in production
  // builds (2026-10-10); actions now return { ok: false, error }.
  await deleteE2eGoals({ titlePrefix: title });
  const post = page.waitForResponse(
    (r) => r.request().method() === "POST" && !!r.request().headers()["next-action"],
  );
  await row(page, title).getByRole("checkbox").click();
  expect((await post).status(), "the action itself answered").toBe(200);

  await expect(week(page).getByRole("alert")).toHaveText("That goal no longer exists.");
  await expect(week(page)).toHaveAttribute("aria-busy", "false");
  // The optimistic "done" is reverted.
  await expect(row(page, title)).toHaveAttribute("data-state", "open");
});

test("an 11th weekly goal shows the cap warning",async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
  await signIn(page, "/goals");
  const held = await week(page).locator('[data-segment="done"], [data-segment="open"]').count();
  await expect(week(page).getByTestId("cap-warning")).toHaveCount(held > 10 ? 1 : 0);
  const stamp = Date.now();
  for (let i = held; i < 11; i++) await addGoal(page, `${runPrefix()}cap ${stamp} ${i + 1}`);
  const total = Math.max(held, 11);
  await expect(week(page).getByTestId("cap-warning")).toHaveText(
    `${total} of 10. Something's not getting done.`,
  );
  await expect(week(page).locator("[data-over]")).toHaveCount(total - 10);
  await expect(week(page).locator("[data-over-cap]")).toHaveCount(total - 10);
  // And it is the server's count, not only the optimistic one.
  await expect(week(page)).toHaveAttribute("aria-busy", "false");
  await page.reload();
  await expect(week(page).getByTestId("cap-warning")).toHaveText(
    `${total} of 10. Something's not getting done.`,
  );
});

test("history renders", async ({ page }) => {
  await signIn(page, "/goals?view=history");
  await expect(page.getByRole("link", { name: "History" })).toHaveAttribute("aria-current", "page");
  const list = page.getByTestId("goals-history");
  const empty = page.getByText("No finished weeks yet.");
  await expect(list.or(empty)).toBeVisible();
  if (await list.isVisible()) {
    await expect(list.getByTestId("history-fraction").first()).toHaveText(/^\d+\/\d+$/);
    await list.locator("summary").first().click();
    await expect(list.locator("details[open] [data-testid=goal-row]").first()).toBeVisible();
  }
});

test("navigation shows only built pages", async ({ page }, testInfo) => {
  await signIn(page, "/");
  if (testInfo.project.name === "phone") {
    const dock = page.getByRole("navigation", { name: "Dock" });
    await expect(dock.getByRole("link")).toHaveText(["Home", "Goals", "Ventures"]);
    // Wags holds the centre: a button that opens the sheet, not a tab.
    await expect(dock.getByRole("listitem").nth(2).getByRole("button", { name: "Wags" })).toBeVisible();
    await expect(dock.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    await dock.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "All pages" });
    await expect(sheet.getByRole("link")).toHaveText([
      "Home",
      "Goals",
      "Wags",
      "Ventures HQ",
      "Capital Tracker",
      "System health",
      "Phone setup",
      "Design tokens",
    ]);
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
  } else {
    const rail = page.getByRole("navigation", { name: "Pages" });
    await expect(rail.getByRole("link")).toHaveText(["Home", "Goals", "Wags", "Ventures HQ", "Capital Tracker"]);
    await expect(page.getByTestId("rail-wags")).toBeVisible();
  }
  // An unbuilt pillar has no page at all.
  const res = await page.goto("/betting");
  expect(res?.status()).toBe(404);
});

test("home shows the date, the ISO week and this week's goals", async ({ page }) => {
  await signIn(page, "/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [A-Z][a-z]+ \d{1,2}$/,
  );
  await expect(page.getByText(/^Week \d{1,2} · Q[1-4]$/)).toBeVisible();
  await expect(page.getByTestId("goals-fraction")).toHaveText(/^\d+ done · \d+ of 10$/);
  await expect(page.getByRole("heading", { level: 2, name: "This week" })).toBeVisible();
  await expect(page.getByTestId("progress-strip")).toBeVisible();
});
