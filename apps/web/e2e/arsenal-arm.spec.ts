import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArsenalRef, addTestSave, armVerifyJobs, arsenalRefKey, arsenalSaves, e2eMarker, hasOwner, lastTestArmRequest,
  MARKER_HEADER, runPrefix, signIn, useE2eMarker,
} from "./support/owner";

// Arm Me (Chat 5 Phase 2; docs/arsenal.md §9.3; db/059), on the MOCK model
// (WAGS_MOCK=1, playwright.config.ts) with the e2e marker, so every card and
// request it writes is a TEST row and no run spends a cent:
//   * three cards stream in, in order: card 1 is on the page before card 3
//     exists, each "Unchecked until saved"; the request is recorded with its
//     timings;
//   * the rate-limit refusal and the budget refusal are readable sentences;
//   * an existing key comes back with its real tag (Corrected), and one the
//     owner saved is "From your Arsenal";
//   * Save on an arm card says "Checking" and queues ONE verify job, ever
//     (save, unsave, save), read back by the marker;
//   * the mock refuses a request without the marker (no real row from e2e).
// Test rows are permanent (append-only); names carry the run prefix.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/arsenal-arm.spec.ts --project=phone --output=test-results/arsenal-arm

const MOCK_TITLES = ["Crossing the Rubicon (mock)", "See's Candies (mock)", "Never tell anybody outside the family (mock)"];

test.describe("logged out", () => {
  test("Arm Me is owner-only", async ({ page }) => {
    await page.goto("/arsenal");
    await expect(page).toHaveURL(/\/login\?next=%2Farsenal/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  let context: BrowserContext;
  let page: Page;

  async function arm(situation: string, room?: string) {
    await page.getByTestId("arm-situation").fill(situation);
    if (room) await page.getByTestId(`arm-room-${room}`).click();
    await page.getByTestId("arm-submit").click();
  }

  /** The next request to the route carries these e2e headers (and the marker). */
  async function withHeaders(extra: Record<string, string>) {
    await page.route("**/api/arsenal/arm", (route) =>
      route.continue({ headers: { ...route.request().headers(), [MARKER_HEADER]: e2eMarker(), ...extra } }), { times: 1 });
  }

  test.beforeAll(async ({ browser }, testInfo) => {
    if (testInfo.project.name !== "phone") return;
    context = await browser.newContext(testInfo.project.use);
    await useE2eMarker(context, testInfo.project.use.baseURL!);
    page = await context.newPage();
    await signIn(page, "/arsenal");
  });

  test.afterAll(async () => {
    await context?.close();
  });

  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "phone", "phone project");
  });

  test("three cards stream in, in order, card 1 before card 3 exists", async () => {
    await page.goto("/arsenal");
    await arm(`${P}slow: a board call on the Q4 raise`);
    const cards = page.getByTestId("arm-card");
    await expect(cards).toHaveCount(1);
    // Card 1 is on the page while the answer is still being written.
    await expect(page.getByTestId("arm-status")).toHaveText(/1 of 3/);
    await expect(cards).toHaveCount(3);
    await expect(cards.getByTestId("card-title")).toHaveText(MOCK_TITLES);
    for (const tag of await cards.getByTestId("card-check").all()) await expect(tag).toHaveText("Unchecked until saved");
    await expect(cards.first().getByTestId("card-deploy")).toHaveText("Once we sign this, we have crossed the Rubicon.");
    await expect(cards.nth(2).getByTestId("card-quote-by")).toHaveText("— Vito Corleone (as written in The Godfather)");
    await expect(page.getByTestId("arm-submit")).toHaveText("Arm me");
    await expect.poll(async () => (await lastTestArmRequest())?.ref_ids.length).toBe(3);
    const req = (await lastTestArmRequest())!;
    expect(req.model).toBe("mock");
    expect(req.error).toBeNull();
    expect(req.first_card_ms).not.toBeNull();
    expect(req.first_card_ms!).toBeLessThan(req.total_ms!);
  });

  test("the rate-limit refusal is a readable sentence", async () => {
    await page.goto("/arsenal");
    await withHeaders({ "x-wags-e2e-rate-limit": "1" });
    await arm(`${P}rate limited`);
    await expect(page.getByTestId("arm-error")).toHaveText("That's 1 in a minute. Give it a moment, then arm up again.");
    await expect(page.getByTestId("arm-card")).toHaveCount(0);
  });

  test("the budget refusal is a readable sentence", async () => {
    await page.goto("/arsenal");
    await withHeaders({ "x-wags-e2e-budget": "over" });
    await arm(`${P}over budget`);
    await expect(page.getByTestId("arm-error")).toHaveText(
      "This month's AI budget is spent ($20.00 of $20.00). Arm Me is back on the 1st, or when the budget is raised.");
  });

  test("an existing key comes back with its real tag, and a saved one is From your Arsenal", async () => {
    const title = `${P}existing ${Date.now()}`;
    const id = await addTestArsenalRef({ category: "finance_business", title, room: "negotiation", verification: "corrected",
                                         note: "the year was 1972" });
    const key = await arsenalRefKey(id);
    await page.goto("/arsenal");
    await arm(`existing: ${key}`);
    const second = page.getByTestId("arm-card").nth(1);
    await expect(second).toHaveAttribute("data-key", key);
    await expect(second.getByTestId("card-title")).toHaveText(title);
    await expect(second.getByTestId("card-check")).toHaveText("Corrected");
    await expect(second.getByTestId("arm-from-arsenal")).toHaveCount(0);
    // Once saved, the same key is "From your Arsenal".
    await addTestSave({ refId: id });
    await page.goto("/arsenal");
    await arm(`existing: ${key}`);
    await expect(page.getByTestId("arm-card").nth(1).getByTestId("arm-from-arsenal")).toHaveText("From your Arsenal");
  });

  test("Save on an arm card says Checking and queues one verify, ever", async () => {
    await page.goto("/arsenal");
    await arm(`${P}save this one`);
    const first = page.getByTestId("arm-card").first();
    await expect(page.getByTestId("arm-card")).toHaveCount(3);
    const id = Number(await first.getAttribute("data-ref-id"));
    const save = first.getByTestId("card-save");
    await expect(save).toHaveAccessibleName("Save");
    const box = (await save.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
    await save.click();
    await expect(save).toHaveAttribute("aria-pressed", "true");
    await expect(first.getByTestId("card-check")).toHaveText("Checking");
    await expect.poll(() => armVerifyJobs(id)).toBe(1);
    await save.click();
    await expect(save).toHaveAttribute("aria-pressed", "false");
    await save.click();
    await expect(save).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => (await arsenalSaves(id)).map((s) => `${s.action}:${s.is_test}`))
      .toEqual(["save:true", "unsave:true", "save:true"]);
    expect(await armVerifyJobs(id)).toBe(1);
  });

  test("the mock refuses a request without the marker", async () => {
    const res = await page.request.post("/api/arsenal/arm", { data: { situation: `${P}no marker` }, headers: {} });
    expect(res.status()).toBe(400);
    expect(await res.text()).toBe("Arm Me's mock model runs only with the e2e marker.");
  });
});
