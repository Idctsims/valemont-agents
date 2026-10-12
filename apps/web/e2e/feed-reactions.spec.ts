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
//   * swipe right = Love, swipe left = Not interested, neither opens it;
//   * opening the publisher writes one 'open' row (the beacon);
//   * the tabs; /settings/feed for the owner.

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

/** Press the centre of a button by coordinates, as a finger does. */
async function tap(target: Locator) {
  await target.scrollIntoViewIfNeeded();
  const box = (await target.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

/** Drag the card body horizontally by `dx` px, pointer down to up. */
async function swipe(card: Locator, dx: number) {
  await card.scrollIntoViewIfNeeded();
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
  await tap(page.getByTestId("feed-undo"));
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
  expect(opened).toEqual([]);
  expect((await storyReactions(a.id)).map((r) => r.reaction)).toEqual(["love"]);
  expect((await storyReactions(b.id)).map((r) => r.reaction)).toEqual(["not_interested"]);
  expect(await storyReactions(c.id)).toEqual([]);
});

test("opening the publisher writes one 'open' row", async () => {
  const { id, card } = await seed("open beacon");
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
    await expect(home.getByTestId("profile-counts")).toBeVisible();
    await expect(home.getByTestId("profile-seed")).toContainText(/seeds still make up \d+%/);
  }
});
