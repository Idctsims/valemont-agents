import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { changeLabel, changeOf, money, signedMoney } from "../src/lib/capital/types";

import {
  MARKER_HEADER,
  RUN_ID,
  capitalEntriesNoted,
  hasOwner,
  signIn,
  snapshotCapital,
  useE2eMarker,
  type CapitalState,
} from "./support/owner";

// Capital Tracker, signed in as the owner's REAL account (owner-only; see
// support/owner.ts). Every request to the app carries the e2e marker
// (db/027), so what this spec writes is a TEST entry: permanent, like every
// entry, but excluded from every real number, snapshot and the normal page.
// A marked request sees the real numbers plus the test entries on top, which
// is how the spec watches its own entry move the page. Every deposit is still
// followed by its correcting adjustment (both noted "e2e <run id>"), and
// afterAll asserts the real paper total and every real entry are unchanged
// and that no real entry was added.
//
// One sign-in for the whole run (a shared page, serial), phone project only.
// Live: this spec only asserts its ABSENCE. A live entry would be permanent
// and real, so the "live appears separately" half is proven in a rolled-back
// database test (workers/tests_live/test_capital_sql.py, TodayView).
//   pnpm test:e2e e2e/capital.spec.ts --project=phone

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
  await useE2eMarker(context, testInfo.project.use.baseURL!);
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
  expect([...after.entries.keys()].sort(), "no real entry was added").toEqual([...before.entries.keys()].sort());
  const mine = await capitalEntriesNoted(NOTE);
  expect(mine.length, "this run wrote its deposit and its correction").toBe(2);
  expect(mine.every((e) => e.isTest && e.mode === "paper"), "this run wrote paper TEST entries only").toBe(true);
  const net = mine.reduce((s, e) => s + (e.kind === "withdrawal" ? -e.amount : e.amount), 0);
  expect(net, "this run's entries net to zero").toBe(0);
});

/** What a marked request shows: the real total plus every test entry. */
const shown = () => before!.paperTotal + before!.testNet;

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
  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
  await expect(paper().getByTestId("mode-tag")).toHaveText(/paper/i);
  await expect(paper().getByTestId("capital-change")).toHaveText(expectedChange(shown()));
  await expect(page.getByTestId("capital-live")).toHaveCount(0);
  await expect(page.getByTestId("mode-tag").filter({ hasText: /live/i })).toHaveCount(0);
});

test("a paper deposit moves the total, breakdown and change with no reload", async () => {
  const bankroll = paper().locator('[data-testid="source-row"][data-source="bankroll"]');
  const bankrollBefore = await bankroll.getByTestId("source-value").textContent();
  const total = shown() + DEPOSIT;

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

  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
  await settled();
  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
  await expect(paper().getByTestId("capital-change")).toHaveText(expectedChange(shown()));
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
  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
  await expect(page.getByTestId("entry-row").filter({ hasText: `${NOTE} must not stick` })).toHaveCount(0);
  await page.unroute("**/*");
  await page.reload();
  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
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

test("this run's entries are listed as test entries", async () => {
  for (const row of [
    page.getByTestId("entry-row").filter({ hasText: `${NOTE} deposit` }),
    page.getByTestId("entry-row").filter({ hasText: `${NOTE} correction` }),
  ]) {
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("test");
  }
});

test("a marker that does not verify is refused, and nothing is written", async () => {
  // Overrides the context's marker for app requests: a wrong marker must
  // fail the save outright, never fall back to writing a real entry.
  await page.route("**/*", (route) =>
    route.request().method() === "POST" && route.request().headers()["next-action"]
      ? route.fallback({ headers: { ...route.request().headers(), [MARKER_HEADER]: "wrong" } })
      : route.fallback(),
  );
  await composer().getByRole("button", { name: "deposit", exact: true }).click();
  await composer().getByRole("textbox", { name: "Amount" }).fill("7.00");
  await composer().getByRole("textbox", { name: "Note" }).fill(`${NOTE} wrong marker`);
  await composer().getByRole("button", { name: "Add +$7.00" }).click();
  await expect(page.getByRole("alert")).toContainText("Test marker refused");
  await settled();
  await page.unroute("**/*");
  await expect(paper().getByTestId("capital-total")).toHaveText(money(shown()));
  expect((await capitalEntriesNoted(`${NOTE} wrong marker`)).length, "nothing was written").toBe(0);
});