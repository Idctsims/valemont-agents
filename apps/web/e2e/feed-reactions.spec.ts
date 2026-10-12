import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { addTestStory, hasOwner, runPrefix, signIn, storyReactions, useE2eMarker } from "./support/owner";

// Reactions on the feed (Chat 3 Phase 2), signed in as the owner's REAL
// account, on TEST stories only (db/032 add_test_story, the e2e marker): a
// test story never reaches a profile, For you, ctx_feed or the brief, so the
// reactions written here (feed_reactions is append-only; they stay) train
// nothing. Each test seeds its own story and touches only it.
//
//   * the buttons never open the publisher; Love marks and toggles;
//   * Not interested and Neutral remove the card with an Undo toast; Undo
//     writes 'undo' and brings it back; a reload keeps a dismissed card gone;
//   * swipe right = Love, swipe left = Not interested, neither opens it; the
//     reveal's icon never clips;
//   * opening the publisher writes one 'open' row (the beacon);
//   * the tabs, aligned with the cards; /settings/feed for the owner, whose
//     pending count never includes a reaction on a test story.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
test.describe.configure({ mode: "serial" });

const P = runPrefix();
const LATEST = "/?feed=latest";
let context: BrowserContext;
let page: Page;
const opened: string[] = [];

test.beforeAll(async ({ browser }, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  context = await browser.newContext(testInfo.project.use);
  await useE2eMarker(context, testInfo.project.use.baseURL!);
  await context.route("https://publisher.e2e.invalid/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<title>publisher</title>" }),
  );
  context.on("page", (p) => opened.push(p.url()));
  page = await context.newPage();
  await signIn(page, LATEST);
});

test.afterAll(async ({}, testInfo) => {
  if (testInfo.project.name !== "phone") return;
  await context?.close();
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "phone project");
});

/** A fresh test story at the top of Latest (newest first). */
async function seed(name: string): Promise<{ id: number; title: string; card: Locator }> {
  const title = `${P}${name}`;
  const id = await addTestStory({ pillar: "home", title, summary: "A test summary.", url: `https://publisher.e2e.invalid/${Date.now()}-${name.replaceAll(" ", "-")}` });
  await page.goto(LATEST);
  const card = page.getByTestId("feed-latest").getByTestId("feed-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  return { id, title, card };
}

async function settled() {
  await expect(page.getByTestId("feed-latest")).toHaveAttribute("aria-busy", "false");
}

/** Scroll to mid-screen, clear of the sticky top bar and the dock's raised
 * Wags button (scrollIntoViewIfNeeded leaves an element that already counts
 * as visible behind them; see feed.spec). */
async function centre(target: Locator) {
  await target.evaluate((el) => el.scrollIntoView({ block: "center" }));
}

/** Press the centre of a button by coordinates, as a finger does. */
async function tap(target: Locator) {
  await centre(target);
  const box = (await target.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

/** Drag the card body horizontally by `dx` px, pointer down to up. */
async function swipe(card: Locator, dx: number) {
  await centre(card.getByTestId("feed-summary"));
  const box = (await card.getByTestId("feed-summary").boundingBox())!;
  const y = box.y + box.height / 2;
  const x = box.x + box.width / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx / 2, y, { steps: 6 });
  await page.mouse.move(x + dx, y, { steps: 6 });
  await page.mouse.up();
}

test("Love marks the card, a second tap takes it back, and the publisher never opens", async () => {
  const { id, card } = await seed("love button");
  opened.length = 0;
  const love = card.getByTestId("react-love");
  await tap(love);
  await expect(love).toHaveAttribute("aria-pressed", "true");
  await settled();
  await page.reload();
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("react-love")).toHaveAttribute("aria-pressed", "true");
  await tap(card.getByTestId("react-love"));
  await expect(card.getByTestId("react-love")).toHaveAttribute("aria-pressed", "false");
  await settled();
  expect(opened).toEqual([]);
  expect((await storyReactions(id)).map((r) => `${r.pillar}:${r.reaction}`)).toEqual(["home:love", "home:undo"]);
});

test("Not interested removes the card with an Undo toast; Undo brings it back", async () => {
  const { id, title, card } = await seed("not interested button");
  opened.length = 0;
  await tap(card.getByTestId("react-not-interested"));
  await expect(card).toHaveCount(0);
  const toast = page.getByTestId("feed-toast");
  await expect(toast).toContainText("Not interested");
  await expect(toast).toContainText(title);
  await settled();
  // Undo is fully on a 390 px screen and nothing covers it (2026-10-12: a long
  // headline pushed it off-screen, so the tap missed and the toast stayed).
  const undo = page.getByTestId("feed-undo");
  await expect(undo).toBeInViewport({ ratio: 1 });
  const ub = (await undo.boundingBox())!;
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest("[data-testid]")?.getAttribute("data-testid"), [ub.x + ub.width / 2, ub.y + ub.height / 2]);
  expect(hit).toBe("feed-undo");
  await tap(undo);
  await expect(toast).toHaveCount(0);
  await expect(card).toHaveCount(1);
  await settled();
  expect(opened).toEqual([]);
  expect((await storyReactions(id)).map((r) => r.reaction)).toEqual(["not_interested", "undo"]);
});

test("Neutral removes the card, and it stays gone after a reload", async () => {
  const { id, card } = await seed("neutral button");
  await tap(card.getByTestId("react-neutral"));
  await expect(card).toHaveCount(0);
  await expect(page.getByTestId("feed-toast")).toContainText("Hidden");
  await settled();
  await page.reload();
  await expect(page.getByTestId("feed-latest")).toBeVisible();
  await expect(page.locator(`[data-story="${id}"]`)).toHaveCount(0);
  expect((await storyReactions(id)).map((r) => r.reaction)).toEqual(["neutral"]);
});

test("swipe right is Love and swipe left is Not interested; neither opens the publisher", async () => {
  const a = await seed("swipe right");
  opened.length = 0;
  await swipe(a.card, 140);
  await expect(a.card.getByTestId("react-love")).toHaveAttribute("aria-pressed", "true");
  await settled();
  const b = await seed("swipe left");
  await swipe(b.card, -140);
  await expect(b.card).toHaveCount(0);
  await expect(page.getByTestId("feed-toast")).toContainText("Not interested");
  await settled();
  // A short drag is neither.
  const c = await seed("short drag");
  await swipe(c.card, 30);
  await page.waitForTimeout(500);
  await expect(c.card.getByTestId("react-love")).toHaveAttribute("aria-pressed", "false");
  // Mid-swipe, at every distance, the reveal is exactly the uncovered strip,
  // carries no text, and its icon is either absent or wholly inside it
  // (2026-10-12: the labels clipped to "LO" and "ED"). Released at zero, so
  // nothing is written.
  await centre(c.card.getByTestId("feed-summary"));
  const sb = (await c.card.getByTestId("feed-summary").boundingBox())!;
  const [x0, y0] = [sb.x + sb.width / 2, sb.y + sb.height / 2];
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  for (const dx of [12, 24, 31, 32, 48, 64, 72, 100, -12, -24, -31, -32, -48, -64, -72, -100]) {
    await page.mouse.move(x0 + dx, y0, { steps: 4 });
    const reveal = c.card.getByTestId("feed-swipe-reveal");
    await expect(reveal).toHaveText("");
    const rb = (await reveal.boundingBox())!;
    const cb = (await c.card.boundingBox())!;
    expect(Math.abs(rb.width - Math.abs(dx))).toBeLessThanOrEqual(1);
    expect(dx > 0 ? rb.x - cb.x : cb.x + cb.width - (rb.x + rb.width)).toBeLessThanOrEqual(1);
    const icon = reveal.locator("svg");
    if (Math.abs(dx) < 32) {
      await expect(icon).toHaveCount(0);
    } else {
      const ib = (await icon.boundingBox())!;
      expect(ib.x).toBeGreaterThanOrEqual(rb.x);
      expect(ib.x + ib.width).toBeLessThanOrEqual(rb.x + rb.width);
    }
  }
  await page.mouse.move(x0, y0, { steps: 4 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  expect(opened).toEqual([]);
  expect((await storyReactions(a.id)).map((r) => r.reaction)).toEqual(["love"]);
  expect((await storyReactions(b.id)).map((r) => r.reaction)).toEqual(["not_interested"]);
  expect(await storyReactions(c.id)).toEqual([]);
});

test("opening the publisher writes one 'open' row", async () => {
  const { id, card } = await seed("open beacon");
  await centre(card.getByTestId("feed-link"));
  const box = (await card.getByTestId("feed-link").boundingBox())!;
  const [popup] = await Promise.all([context.waitForEvent("page"), page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)]);
  await popup.close();
  await expect.poll(async () => (await storyReactions(id)).map((r) => r.reaction), { timeout: 10_000 }).toEqual(["open"]);
  // A second open inside ten minutes is not more signal.
  const [again] = await Promise.all([context.waitForEvent("page"), page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)]);
  await again.close();
  await page.waitForTimeout(1500);
  expect((await storyReactions(id)).map((r) => r.reaction)).toEqual(["open"]);
});

test("the tabs: For you is the default, Latest is one tap away", async () => {
  await page.goto("/");
  await expect(page.getByTestId("feed-tab-for-you")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("feed-tab-latest")).not.toHaveAttribute("aria-current", "page");
  // For you never shows a test story, even to a marked request.
  await expect(page.getByTestId("feed-test")).toHaveCount(0);
  await page.getByTestId("feed-tab-latest").click();
  await expect(page).toHaveURL(/feed=latest/);
  await expect(page.getByTestId("feed-tab-latest")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("feed-latest")).toBeVisible();
  // The tab row's first pill starts on the cards' left edge (2026-10-12: it
  // started 12 px left of it).
  const pill = (await page.getByTestId("feed-tab-for-you").boundingBox())!;
  const card = (await page.getByTestId("feed-latest").getByTestId("feed-card").first().boundingBox())!;
  expect(Math.abs(pill.x - card.x)).toBeLessThanOrEqual(1);
});

test("/settings/feed shows the profile, the follows and the rebuilds", async () => {
  await page.goto("/settings/feed");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Feed profile");
  await expect(page.getByTestId("profile-pending")).toBeVisible();
  const follows = page.getByTestId("profile-follows");
  await expect(follows.getByTestId("follow").filter({ hasText: "Alabama Crimson Tide" })).toContainText("Near the top");
  await expect(follows.getByTestId("follow").filter({ hasText: "LeBron James" })).toHaveCount(1);
  const home = page.getByTestId("profile-home");
  if (await home.count()) {
    const counts = home.getByTestId("profile-counts");
    await expect(counts).toBeVisible();
    // The Not interested count is "Hidden": "Not interested" wrapped in a
    // quarter of 390 px, and "Skipped" read as no tap (owner, 2026-10-12).
    // Every label is one line.
    await expect(counts.locator("dt")).toHaveText(["Loved", "Hidden", "Opened", "Neutral"]);
    await expect(counts).not.toContainText(/Skipped|Not interested/);
    const history = page.getByTestId("profile-history-row");
    if (await history.count()) {
      await expect(history.first()).toContainText(/hidden \d+/);
      await expect(history.first()).not.toContainText(/not interested|skipped/i);
    }
    const heights = await counts.locator("dt").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
    expect(new Set(heights).size).toBe(1); // "Loved" is one line, so all are
    await expect(home.getByTestId("profile-seed")).toContainText(/seeds still make up \d+%/);
  }
});

test("a reaction on a test story never counts on /settings/feed", async () => {
  // 2026-10-12: "9 reactions since the last rebuild", all from these specs.
  await page.goto("/settings/feed");
  const pending = page.getByTestId("profile-pending");
  const before = await pending.textContent();
  const { card } = await seed("pending count");
  await tap(card.getByTestId("react-love"));
  await expect(card.getByTestId("react-love")).toHaveAttribute("aria-pressed", "true");
  await settled();
  await page.goto("/settings/feed");
  await expect(pending).toHaveText(before!);
});
