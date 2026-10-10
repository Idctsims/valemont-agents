import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  deleteGoalIds,
  deleteWagsThreads,
  goalIdsTitled,
  hasOwner,
  runPrefix,
  signIn,
  ventureSlugNamed,
  wagsMessages,
} from "./support/owner";

// Wags, signed in as the owner's REAL account, against the MOCK model: the
// e2e server runs with WAGS_MOCK=1 (playwright.config.ts) and no Vercel env,
// so src/lib/ai/policy.ts mockAllowed() hands /api/wags the scripted model in
// src/lib/wags/mock.ts. No test here spends a cent: the first test checks the
// footer says "Mock" before anything is sent, and stops the run if not.
//
// Each test touches only its own rows: the threads it started (deleted by id
// at the end, their messages with them) and the one goal it confirmed
// (deleted by id). Mock turns write no ai_usage and no context_snapshots row.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

let context: BrowserContext;
let page: Page;
const threads: string[] = [];
const goals: string[] = [];
const P = runPrefix();

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  context = await browser.newContext(testInfo.project.use);
  page = await context.newPage();
  await signIn(page, "/");
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
  await deleteGoalIds(goals);
  await deleteWagsThreads(threads);
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

const sheet = () => page.getByTestId("wags-sheet");
const composer = () => sheet().getByRole("textbox", { name: "Message Wags" });

async function openSheet() {
  await page.getByTestId("dock-wags").click();
  await expect(sheet()).toBeVisible();
  await expect(composer()).toBeVisible();
}

async function closeSheet() {
  await sheet().getByRole("button", { name: "Close", exact: true }).click();
  await expect(sheet()).toHaveCount(0);
}

/** The thread the sheet is on (remembered once Wags has answered). */
async function currentThread(): Promise<string> {
  // Stored when the answer's stream finishes, a beat after its text shows.
  const read = () => page.evaluate(() => sessionStorage.getItem("vm-wags-thread"));
  await expect.poll(read, { message: "the sheet remembers its thread" }).toMatch(/^[0-9a-f-]{36}$/);
  const id = (await read())!;
  threads.push(id);
  return id;
}

async function send(text: string) {
  await composer().fill(text);
  await composer().press("Enter");
}

const lastWags = () => sheet().getByTestId("wags-turn").last();

test("the sheet opens from Ventures · Sail Beach Club with that context, on the mock model", async () => {
  const slug = await ventureSlugNamed("Sail Beach Club");
  test.skip(!slug, "no venture named Sail Beach Club");
  await page.goto(`/ventures/${slug}`);
  await openSheet();
  await expect(sheet().getByTestId("wags-context-chip")).toHaveText("Context · Ventures · Sail Beach Club");
  // The guard against spending: the server must be in mock mode.
  await expect(sheet().getByTestId("wags-status")).toHaveText(/^Mock · \$\d+\.\d{2} of \$\d+ this month$/);
});

test("a message streams, names the context it was given, and survives a reload", async () => {
  await send(`${P}hello`);
  // The turn appears while it streams (the pulse sits inside it), then completes.
  await expect(lastWags()).toBeVisible();
  await expect(lastWags()).toContainText("Mock Wags.");
  await expect(lastWags()).toContainText("Context: goals, ventures, capital");
  await expect(lastWags()).toContainText("Page: Ventures · Sail Beach Club");
  const id = await currentThread();

  await page.reload();
  await openSheet();
  await expect(sheet().getByTestId("tsims-turn")).toContainText(`${P}hello`);
  await expect(lastWags()).toContainText("Mock Wags.");
  const stored = await wagsMessages(id);
  expect(stored.map((m) => m.role)).toEqual(["user", "assistant"]);
  expect(stored[1].model, "served by the mock, never a real model").toBe("mock");
});

test("removing the context chip sends the next message without it", async () => {
  await sheet().getByRole("button", { name: "Remove page context" }).click();
  await expect(sheet().getByTestId("wags-context-chip")).toHaveCount(0);
  await send(`${P}no page`);
  await expect(lastWags()).toContainText("Page: none.");
});

test("a proposed goal, confirmed, exists; then it is removed by id", async () => {
  const title = `${P}wags goal`;
  await send(`propose goal: ${title}`);
  const row = sheet().getByTestId("wags-proposal").last();
  await expect(row).toHaveAttribute("data-state", "pending");
  await expect(row).toContainText(`Add “${title}” · this week · business`);
  await row.getByRole("button", { name: "Confirm" }).click();
  await expect(row).toHaveAttribute("data-state", "confirmed");
  await expect(row).toContainText("Added to this week.");

  const ids = await goalIdsTitled(title);
  expect(ids, "exactly one goal was created").toHaveLength(1);
  goals.push(...ids);

  // The outcome is in the thread: it is still confirmed after a reload.
  await page.reload();
  await openSheet();
  await expect(sheet().getByTestId("wags-proposal").last()).toHaveAttribute("data-state", "confirmed");
  await deleteGoalIds(ids);
  expect(await goalIdsTitled(title)).toHaveLength(0);
});

test("a dismissed proposal writes nothing and stays dismissed", async () => {
  const title = `${P}wags dismissed`;
  await send(`propose goal: ${title}`);
  const row = sheet().getByTestId("wags-proposal").last();
  await row.getByRole("button", { name: "Dismiss" }).click();
  await expect(row).toHaveAttribute("data-state", "dismissed");
  expect(await goalIdsTitled(title)).toHaveLength(0);
  await page.reload();
  await openSheet();
  await expect(sheet().getByTestId("wags-proposal").last()).toHaveAttribute("data-state", "dismissed");
});

test("the rate limit refuses in a plain sentence and keeps the words", async () => {
  // Lower the limit for this request only (honoured in mock mode alone).
  // At least one user message is in the last minute (the dismiss test's).
  await page.route("**/api/wags", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-wags-e2e-rate-limit": "1" } }),
  );
  await send(`${P}one too many`);
  await expect(sheet().getByTestId("wags-refusal")).toHaveText("That's 1 message in a minute. Give it a moment, then ask again.");
  await expect(composer()).toHaveValue(`${P}one too many`);
  await page.unroute("**/api/wags");
  const stored = await wagsMessages(threads[0]);
  expect(stored.some((m) => m.content.includes("one too many")), "a refused message is not stored").toBe(false);
  await composer().fill("");
});

test("a message over 4,000 characters is refused by the route in a sentence", async () => {
  const res = await page.request.post("/api/wags", {
    data: { threadId: threads[0], message: { parts: [{ type: "text", text: "x".repeat(4001) }] } },
  });
  expect(res.status()).toBe(413);
  expect(await res.text()).toBe("That's 4,001 characters. Keep it under 4,000.");
});

test("an AI budget that is spent refuses in a sentence; from 80% Wags says budget mode", async () => {
  await page.route("**/api/wags", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-wags-e2e-budget": "over" } }),
  );
  await send(`${P}over budget`);
  await expect(sheet().getByTestId("wags-refusal")).toContainText("This month's AI budget is spent");
  await expect(sheet().getByTestId("wags-refusal")).not.toContainText(/\b402\b|error/i);
  await page.unroute("**/api/wags");

  await page.route("**/api/wags", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-wags-e2e-budget": "downgrade" } }),
  );
  await composer().fill(`${P}budget mode`);
  await composer().press("Enter");
  await expect(lastWags()).toContainText("Mock Wags.");
  await expect(sheet().getByTestId("wags-status")).toContainText("Haiku · budget mode");
  await page.unroute("**/api/wags");
});

test("a new thread shows the four starters, and a starter asks", async () => {
  await sheet().getByRole("button", { name: "New thread" }).click();
  const starters = sheet().getByTestId("wags-starters");
  await expect(starters.getByRole("button")).toHaveText([
    /What should I focus on today\?/,
    /What's due this week\?/,
    /Pressure-test an idea/,
    /Summarize my week/,
  ]);
  await starters.getByRole("button", { name: /Pressure-test an idea/ }).click();
  await expect(composer()).toHaveValue("Pressure-test this idea: ");
  await composer().fill("");
  await starters.getByRole("button", { name: /What's due this week\?/ }).click();
  await expect(sheet().getByTestId("tsims-turn")).toHaveText(/What's due this week\?/);
  await expect(lastWags()).toContainText("Mock Wags.");
  await currentThread();
  await closeSheet();
});

test("on /wags a thread can be renamed and archived", async () => {
  const id = threads[0];
  await page.goto(`/wags?t=${id}`);
  await page.getByRole("button", { name: /^Threads · \d+$/ }).click();
  await expect(page.locator(`[data-thread="${id}"]`), "each thread is listed once").toHaveCount(1);
  const row = page.locator(`[data-testid="wags-thread"][data-thread="${id}"]`);
  await row.getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const input = row.getByRole("textbox", { name: "Thread title" });
  await input.fill(`${P}renamed`);
  await input.press("Enter");
  await expect(row).toContainText(`${P}renamed`);

  await row.getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  // Archived threads fold away under "Archived · N".
  await page.getByTestId("wags-archived").getByRole("button", { name: /^Archived · \d+$/ }).click();
  await expect(row).toHaveAttribute("data-archived", "true");
  await expect(row).toContainText(`${P}renamed`);
});
