import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { changeLabel, changeOf, money, signedMoney } from "../src/lib/capital/types";

import { RUN_ID, capitalEntriesNoted, hasOwner, signIn, snapshotCapital, type CapitalState } from "./support/owner";

// Capital Tracker, signed in as the owner's REAL account (owner-only; see
// support/owner.ts). Bankroll entries are append-only, so nothing this spec
// writes can be deleted. To keep the real paper bankroll clean, every
// deposit is immediately followed by its correcting adjustment, both noted
// "e2e <run id>", and afterAll asserts the paper total is exactly what it
// was before the run and every pre-existing entry is untouched.
//
// One sign-in for the whole run (a shared page, serial), phone project only.
// Live: this spec only asserts its ABSENCE. A live entry would be permanent
// and real, so the "live appears separately" half is proven in a rolled-back
// database test (workers/tests_live/test_capital_sql.py, TodayView).

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

const NOTE = `e2e ${RUN_ID}`;
const DEPOSIT = 1234; // $12.34, cents

let before: CapitalState | null = null;
let context: BrowserContext | null = null;
let page: Page;

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  before = await snapshotCapital();
  context = await browser.newContext(testInfo.project.use);
  page = await context.newPage();
  await signIn(page, "/capital");
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
  if (!before) return; // beforeAll failed; that failure is the report
  const after = await snapshotCapital();
  expect(after.paperTotal, "the real paper total is unchanged").toBe(before.paperTotal);
  expect(after.liveRows, "still no live rows").toBe(before.liveRows);
  for (const [id, row] of before.entries) expect(after.entries.get(id), `entry ${id} changed`).toBe(row);
  const mine = await capitalEntriesNoted(NOTE);
  const added = [...after.entries.keys()].filter((id) => !before!.entries.has(id));
  expect(added.sort(), "only this run's entries were added").toEqual(mine.map((e) => e.id).sort());
  const net = mine.reduce((s, e) => s + (e.kind === "withdrawal" ? -e.amount : e.amount), 0);
  expect(net, "this run's entries net to zero").toBe(0);
  expect(mine.every((e) => e.mode === "paper"), "this run wrote paper only").toBe(true);
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

const paper = () => page.getByTestId("capital-paper");
const composer = () => page.getByTestId("entry-composer");

async function settled() {
  await expect(page.getByTestId("capital")).toHaveAttribute("aria-busy", "false");
}

function expectedChange(total: number): string {
  return changeLabel(changeOf({ source: "total", value: total, prior: before!.paperPrior }));
}

test("the hero is the paper total, tagged PAPER, and LIVE is absent", async () => {
  await expect(paper().getByTestId("capital-total")).toHaveText(money(before!.paperTotal));
  await expect(paper().getByTestId("mode-tag")).toHaveText(/paper/i);
  await expect(paper().getByTestId("capital-change")).toHaveText(expectedChange(before!.paperTotal));
  await expect(page.getByTestId("capital-live")).toHaveCount(0);
  await expect(page.getByTestId("mode-tag").filter({ hasText: /live/i })).toHaveCount(0);
});

test("a paper deposit moves the total, breakdown and change with no reload", async () => {
  const bankroll = paper().locator('[data-testid="source-row"][data-source="bankroll"]');
  const bankrollBefore = await bankroll.getByTestId("source-value").textContent();
  const total = before!.paperTotal + DEPOSIT;

  await composer().getByRole("button", { name: "deposit", exact: true }).click();
  await composer().getByRole("textbox", { name: "Amount" }).fill("12.34");
  await composer().getByRole("textbox", { name: "Note" }).fill(`${NOTE} deposit`);
  await composer().getByRole("button", { name: `Add ${signedMoney(DEPOSIT)}` }).click();

  // At once (optimistic), and still after the save lands (server render), no reload.
  for (const phase of ["optimistic", "saved"]) {
    if (phase === "saved") await settled();
    await expect(paper().getByTestId("capital-total"), phase).toHaveText(money(total));
    await expect(paper().getByTestId("capital-change"), phase).toHaveText(expectedChange(total));
    await expect(bankroll.getByTestId("source-value"), phase).not.toHaveText(bankrollBefore!);
  }
  const row = page.getByTestId("entry-row").filter({ hasText: `${NOTE} deposit` });
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("entry-amount")).toHaveText("+$12.34");
  await expect(row).not.toContainText("saving");
});

test("Correct with adjustment pre-fills the negation and restores the prior total", async () => {
  const row = page.getByTestId("entry-row").filter({ hasText: `${NOTE} deposit` });
  await row.getByRole("button", { name: "Entry actions" }).click();
  await page.getByRole("menuitem", { name: "Correct with adjustment" }).click();

  await expect(composer().getByRole("button", { name: "adjustment", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(composer().getByRole("button", { name: "− subtract", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(composer().getByRole("textbox", { name: "Amount" })).toHaveValue("12.34");
  await expect(composer().getByRole("button", { name: "paper", exact: true })).toHaveAttribute("aria-pressed", "true");
  await composer().getByRole("textbox", { name: "Note" }).fill(`${NOTE} correction`);
  await composer().getByRole("button", { name: `Add ${signedMoney(-DEPOSIT)}` }).click();

  await expect(paper().getByTestId("capital-total")).toHaveText(money(before!.paperTotal));
  await settled();
  await expect(paper().getByTestId("capital-total")).toHaveText(money(before!.paperTotal));
  await expect(paper().getByTestId("capital-change")).toHaveText(expectedChange(before!.paperTotal));
  const fix = page.getByTestId("entry-row").filter({ hasText: `${NOTE} correction` });
  await expect(fix.getByTestId("entry-amount")).toHaveText("−$12.34");
  await expect(fix).toHaveAttribute("data-kind", "adjustment");
});

test("a failed save reverts and says so", async () => {
  // Fail the next Server Action at the network: nothing reaches the
  // database, and the screen must not keep the optimistic entry.
  await page.route("**/*", (route) =>
    route.request().method() === "POST" && route.request().headers()["next-action"] ? route.abort() : route.fallback(),
  );
  await composer().getByRole("button", { name: "deposit", exact: true }).click();
  await composer().getByRole("textbox", { name: "Amount" }).fill("5.00");
  await composer().getByRole("textbox", { name: "Note" }).fill(`${NOTE} must not stick`);
  await composer().getByRole("button", { name: "Add +$5.00" }).click();

  await expect(page.getByRole("alert")).toBeVisible();
  await settled();
  await expect(paper().getByTestId("capital-total")).toHaveText(money(before!.paperTotal));
  await expect(page.getByTestId("entry-row").filter({ hasText: `${NOTE} must not stick` })).toHaveCount(0);
  await page.unroute("**/*");
  await page.reload();
  await expect(paper().getByTestId("capital-total")).toHaveText(money(before!.paperTotal));
  await expect(page.getByTestId("entry-row").filter({ hasText: `${NOTE} must not stick` })).toHaveCount(0);
});

test("switching to LIVE needs LIVE typed before anything can be added", async () => {
  await composer().getByRole("textbox", { name: "Amount" }).fill("1.00");
  await composer().getByRole("button", { name: "live", exact: true }).click();
  const add = composer().getByRole("button", { name: "Add +$1.00" });
  await expect(page.getByTestId("live-confirm")).toBeVisible();
  await expect(add).toBeDisabled();
  await page.getByTestId("live-confirm").getByRole("textbox").fill("live");
  await expect(add).toBeDisabled(); // exactly LIVE, not a near miss
  // Never submitted: back to paper, and the confirmation is gone.
  await composer().getByRole("button", { name: "paper", exact: true }).click();
  await expect(page.getByTestId("live-confirm")).toHaveCount(0);
  await composer().getByRole("textbox", { name: "Amount" }).fill("");
  await expect(page.getByTestId("capital-live")).toHaveCount(0);
});
