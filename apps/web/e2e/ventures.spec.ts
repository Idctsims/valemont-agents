import { expect, test, type Page } from "@playwright/test";

import { localToday } from "../src/lib/goals/period";

import {
  createTempVenture,
  deleteVenturesById,
  hasOwner,
  signIn,
  snapshotVentures,
} from "./support/owner";

// Ventures HQ, signed in as the owner's REAL account (owner-only; see
// support/owner.ts). Everything happens on a temporary venture created by id
// ("e2e <run id> …") and deleted by id afterwards; deleting it cascades its
// workstreams, dates and log. The owner's real ventures are snapshotted
// first and checked unchanged at the end. Phone project only, serial: the
// steps build on one venture.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

let real = new Map<string, string>();
let temp: { id: string; slug: string; name: string } | null = null;

test.beforeAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  real = await snapshotVentures();
  temp = await createTempVenture("hq", { stage: "idea", next_action: "First move" });
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await deleteVenturesById(temp ? [temp.id] : []);
  const after = await snapshotVentures();
  expect([...after.keys()].sort(), "no real venture row added or removed").toEqual([...real.keys()].sort());
  for (const [key, value] of real) expect(after.get(key), `${key} changed`).toBe(value);
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

const detail = (page: Page) => page.getByTestId("venture-detail");

async function settled(page: Page) {
  await expect(detail(page)).toHaveAttribute("aria-busy", "false");
}

test("a workstream date due today shows in Home TODAY, and leaves when done", async ({ page }) => {
  await signIn(page, `/ventures/${temp!.slug}`);
  await page.getByRole("button", { name: "+ Workstream" }).click();
  await page.getByRole("textbox", { name: "Workstream name" }).fill("Track");
  await page.getByRole("textbox", { name: "Workstream name" }).press("Enter");
  await settled(page);
  await page.reload();

  await detail(page).getByTestId("workstream-row").filter({ hasText: "Track" }).getByRole("button").first().click();
  const label = `${temp!.name} due today`;
  await detail(page).getByRole("button", { name: "+ Date" }).first().click();
  await page.getByRole("textbox", { name: "Date label" }).fill(label);
  await page.getByLabel("Due on").fill(localToday());
  await page.getByTestId("date-form").getByRole("button", { name: "Add" }).click();
  await settled(page);

  await page.goto("/");
  const today = page.getByTestId("venture-today");
  const row = today.getByTestId("today-row").filter({ hasText: label });
  await expect(row).toBeVisible();
  await expect(row).toContainText(`${temp!.name} · Track`);
  await row.getByRole("checkbox", { name: `Mark ${label} done` }).click();
  await expect(row).toHaveCount(0); // optimistic
  // Wait for the save itself before the reload (the module may vanish
  // entirely once no row is due, so wait on "nothing busy", not on it).
  await expect(page.locator('[data-testid="venture-today"][aria-busy="true"]')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("today-row").filter({ hasText: label })).toHaveCount(0);
});

test("editing the next action writes an auto log entry", async ({ page }) => {
  await signIn(page, `/ventures/${temp!.slug}`);
  await detail(page).getByTestId("venture-next").click();
  await page.getByRole("textbox", { name: "Next action" }).fill("Second move");
  await page.getByRole("button", { name: "Save" }).click();
  await settled(page);
  await page.reload();
  await expect(detail(page).getByTestId("venture-next")).toHaveText("Second move");
  await expect(
    detail(page).locator('[data-testid="log-entry"][data-kind="auto"]').filter({ hasText: "Next action: First move → Second move" }),
  ).toHaveCount(1);
});

test("a decision goes into the log, marked", async ({ page }) => {
  await signIn(page, `/ventures/${temp!.slug}`);
  const composer = page.getByTestId("log-composer");
  await composer.getByRole("button", { name: "decision" }).click();
  await composer.getByRole("textbox", { name: "Log entry" }).fill(`${temp!.name} decided`);
  await composer.getByRole("button", { name: "Add" }).click();
  await settled(page);
  await page.reload();
  const entry = detail(page).locator('[data-testid="log-entry"][data-kind="decision"]').filter({ hasText: `${temp!.name} decided` });
  await expect(entry).toHaveCount(1);
  await expect(entry).toContainText("decision");
});

test("archive moves it to the Archived fold; restore brings it back", async ({ page }) => {
  await signIn(page, `/ventures/${temp!.slug}`);
  await page.getByRole("button", { name: "Venture actions" }).click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  await settled(page);
  await expect(detail(page)).toContainText("archived");

  await page.goto("/ventures");
  const mine = page.locator(`[data-testid="venture-row"][data-slug="${temp!.slug}"]`);
  await expect(page.getByTestId("ventures").locator(`[data-slug="${temp!.slug}"]`)).toHaveCount(0);
  await page.getByTestId("ventures-archived").getByRole("button", { name: /^Archived · \d+$/ }).click();
  await expect(mine).toBeVisible();

  await page.goto(`/ventures/${temp!.slug}`);
  await page.getByRole("button", { name: "Venture actions" }).click();
  await page.getByRole("menuitem", { name: "Restore" }).click();
  await settled(page);
  await page.goto("/ventures");
  await expect(page.getByTestId("ventures").locator(`[data-slug="${temp!.slug}"]`)).toHaveCount(1);
});

test("a failed save reverts and says so", async ({ page }) => {
  await signIn(page, `/ventures/${temp!.slug}`);
  const before = await detail(page).getByTestId("venture-next").textContent();
  // Fail the next Server Action at the network: the app must not keep the
  // optimistic value.
  await page.route("**/*", (route) =>
    route.request().method() === "POST" && route.request().headers()["next-action"] ? route.abort() : route.fallback(),
  );
  await detail(page).getByTestId("venture-next").click();
  await page.getByRole("textbox", { name: "Next action" }).fill("This must not stick");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(detail(page).getByRole("alert")).toBeVisible();
  await settled(page);
  await expect(detail(page).getByTestId("venture-next")).toHaveText(before!);
  await page.unroute("**/*");
  await page.reload();
  await expect(detail(page).getByTestId("venture-next")).toHaveText(before!);
});
