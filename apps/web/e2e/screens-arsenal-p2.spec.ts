import { expect, test, type Page } from "@playwright/test";

import { addTestArmRef, addTestSave, hasOwner, runPrefix, setTheme, signIn, useE2eMarker } from "./support/owner";

// Screenshot gate for Chat 5 Phase 2: Arm Me (three mock cards streamed in,
// one saved), My Arsenal (the test saves, then a filter chip on), and the
// Monday quiz (a fresh test quiz: a question, then the same question
// answered). The lock state is the quiz spec's. All on TEST rows with the
// marker and the mock model.
// Both themes, phone 390x844 and desktop 1440. Written to
// e2e/screenshots/chat5-p2/ (gitignored). Needs db/059.
//   pnpm test:e2e e2e/screens-arsenal-p2.spec.ts --project=phone --workers=1 --output=test-results/screens-arsenal-p2-phone
//   pnpm test:e2e e2e/screens-arsenal-p2.spec.ts --project=desktop --workers=1 --output=test-results/screens-arsenal-p2-desktop

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat5-p2";
const THEMES = ["night", "day"] as const;

// The fixed dock and the sticky phone header, hidden for the capture only
// (a full-page capture paints them mid-page); the app is unchanged.
const HIDE_CHROME = 'nav[aria-label="Dock"], header.sticky { visibility: hidden !important; }';

async function snap(page: Page, name: string, phone: boolean, theme: string) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${DIR}/${name}-${phone ? "390" : "1440"}-${theme}.png`, fullPage: true, style: HIDE_CHROME });
}

test("arsenal phase 2 screens", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  test.setTimeout(300_000);
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  await signIn(page, "/arsenal");

  // Five saved test cards (distinct meanings) for My Arsenal and the quiz.
  const P = runPrefix();
  const stamp = Date.now();
  const rooms = ["boardroom", "pitch", "negotiation", "homies", "pitch"];
  const cats = ["history_military", "finance_business", "film_tv", "sports", "music_culture"];
  for (let i = 0; i < 5; i++) {
    const { id } = await addTestArmRef(`${P}Saved card ${i + 1}`, cats[i], rooms[i]);
    await addTestSave({ refId: id });
  }

  for (const theme of THEMES) {
    await setTheme(page, theme);

    await page.goto("/arsenal");
    await page.getByTestId("arm-situation").fill(`${P}Board call ${stamp}: the co-founder wants to delay the raise.`);
    await page.getByTestId("arm-room-boardroom").click();
    await page.getByTestId("arm-submit").click();
    await expect(page.getByTestId("arm-card")).toHaveCount(3);
    await page.getByTestId("arm-card").first().getByTestId("card-save").click();
    await expect(page.getByTestId("arm-card").first().getByTestId("card-check")).toHaveText("Checking");
    await snap(page, "arm-me", phone, theme);

    await page.goto("/arsenal/saved");
    await expect(page.getByTestId("saved-item").first()).toBeVisible();
    await snap(page, "my-arsenal", phone, theme);
    await page.getByTestId("saved-filter-room-pitch").click();
    await snap(page, "my-arsenal-filtered", phone, theme);

    await page.goto(theme === "night" ? "/arsenal/quiz?fresh=1" : "/arsenal/quiz");
    await expect(page.getByTestId("quiz-progress")).toBeVisible();
    await snap(page, "quiz-question", phone, theme);
    await page.getByTestId("quiz-option").first().click();
    await expect(page.getByTestId("quiz-verdict")).toBeVisible();
    await snap(page, "quiz-answered", phone, theme);
  }
});
