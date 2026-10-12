import { expect, test, type Page } from "@playwright/test";

import { hasOwner, setTheme, signIn } from "./support/owner";

// Screenshot review for Chat 3 Phase 2: For you, a card mid-swipe (both
// directions, phone) and /settings/feed, in both themes, on the REAL feed
// (no test marker). Phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat3-p2/ (gitignored).
//   pnpm test:e2e e2e/screens-feed-ranking.spec.ts --workers=1
//
// The mid-swipe shots hold the drag, then bring the card back to zero before
// releasing, so no reaction is ever written to a real story.

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat3-p2";

async function snap(page: Page, name: string, phone: boolean, theme: string, fullPage = !phone) {
  const w = phone ? "390" : "1440";
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage });
}

/** Hold a horizontal drag of `dx` px on the first For you card, shoot, then
 * return to the start and release: below the commit distance, nothing is written. */
async function midSwipe(page: Page, dx: number, name: string, phone: boolean, theme: string) {
  const card = page.getByTestId("feed-for-you").getByTestId("feed-card").first();
  await card.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -80));
  const box = (await card.getByTestId("feed-link").boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y, { steps: 8 });
  await snap(page, name, phone, theme, false);
  await page.mouse.move(x, y, { steps: 8 });
  await page.mouse.up();
}

test("feed ranking screens", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, "/");

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.goto("/#stories");
    await expect(page.getByTestId("feed-tab-for-you")).toHaveAttribute("aria-current", "page");
    await page.getByTestId("feed").scrollIntoViewIfNeeded();
    await snap(page, "home-for-you", phone, theme);

    const why = page.getByTestId("feed-why").first();
    if (await why.count()) {
      await why.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -160));
      await snap(page, "home-for-you-why", phone, theme, false);
    }

    if (phone) {
      await midSwipe(page, 40, "card-swipe-love-short", phone, theme);
      await midSwipe(page, 64, "card-swipe-love", phone, theme);
      await midSwipe(page, -64, "card-swipe-not-interested", phone, theme);
    }

    await page.goto("/settings/feed");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Feed profile");
    await snap(page, "settings-feed", phone, theme);
    if (phone) {
      await page.getByTestId("profile-follows").scrollIntoViewIfNeeded();
      await snap(page, "settings-feed-follows", phone, theme);
    }
  }
});
