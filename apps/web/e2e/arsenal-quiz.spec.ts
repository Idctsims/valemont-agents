import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import {
  addTestArmRef, addTestArsenalRef, addTestSave, clearTestSaves, hasOwner, lastTestQuiz, quizReviews, runPrefix, signIn,
  useE2eMarker,
} from "./support/owner";

// The Monday quiz (Chat 5 Phase 2; docs/arsenal.md §9.6; db/059), as a TEST
// quiz of TEST saves (the marker), never the real week's:
//   * with fewer than 4 saved, the page shows the lock state;
//   * with 5 saved references, a 10-question quiz builds (each asked both
//     ways), every answer writes one review row (rated good or again, with
//     its FSRS card), re-opening resumes at the first unanswered question,
//     and the end shows the score and the review list.
// The lock check first unsaves every test save on record (append-only
// unsave events, test rows only), so run this spec on its own.
//
// Run (owner, CLAUDE.md §6, the Read-Host form):
//   pnpm test:e2e e2e/arsenal-quiz.spec.ts --project=phone --workers=1 --output=test-results/arsenal-quiz

test.describe("logged out", () => {
  test("the quiz asks for sign-in", async ({ page }) => {
    await page.goto("/arsenal/quiz");
    await expect(page).toHaveURL(/\/login\?next=%2Farsenal%2Fquiz/);
  });
});

test.describe("owner", () => {
  test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");
  test.describe.configure({ mode: "serial" });

  const P = runPrefix();
  const stamp = Date.now();
  let context: BrowserContext;
  let page: Page;

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

  test("fewer than 4 saved shows the lock state", async () => {
    await clearTestSaves();
    const one = await addTestArsenalRef({ category: "sports", title: `${P}lonely ${stamp}`, room: "homies" });
    await addTestSave({ refId: one });
    await page.goto("/arsenal/quiz?fresh=1");
    await expect(page.getByTestId("quiz-locked")).toContainText("Save at least 4 to unlock the quiz.");
    await expect(page.getByTestId("quiz-locked")).toContainText("1 saved so far");
    await expect(page.getByTestId("quiz")).toHaveCount(0);
  });

  test("a 10-question quiz builds, answers write reviews, re-opening resumes, the score shows", async () => {
    // Five saved references with distinct titles AND meanings (add_test_arm_ref
    // names the title in the meaning), so both question kinds can be asked.
    await clearTestSaves();
    const cats = ["history_military", "finance_business", "film_tv", "music_culture", "sports"] as const;
    for (const [i, c] of cats.entries()) {
      const { id } = await addTestArmRef(`${P}quiz ${i} ${stamp}`, c, "pitch");
      await addTestSave({ refId: id });
    }
    await page.goto("/arsenal/quiz?fresh=1");
    await expect(page.getByTestId("quiz-progress")).toHaveText("Question 1 of 10");
    const quiz = (await lastTestQuiz())!;
    expect(quiz.questions).toBe(10);

    async function answer(no: number) {
      await expect(page.getByTestId("quiz-progress")).toHaveText(`Question ${no} of 10`);
      const options = page.getByTestId("quiz-option");
      await expect(options).toHaveCount(4);
      const box = (await options.first().boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
      await options.nth(no % 4).click();
      await expect(page.getByTestId("quiz-verdict")).toHaveText(/^(Right\.|Not this time\.)$/);
      // The right answer is marked either way.
      await expect(page.locator('[data-testid="quiz-option"][data-state="right"]')).toHaveCount(1);
      await page.getByTestId("quiz-next").click();
    }

    for (const no of [1, 2, 3]) await answer(no);
    await expect.poll(async () => (await quizReviews(quiz.id)).length).toBe(3);
    // Re-opening (no ?fresh) resumes at the first unanswered question.
    await page.goto("/arsenal/quiz");
    for (const no of [4, 5, 6, 7, 8, 9, 10]) await answer(no);

    await expect(page.getByTestId("quiz-score")).toHaveText(/^\d+ of 10$/);
    await expect(page.getByTestId("quiz-review-item")).toHaveCount(10);
    const reviews = await quizReviews(quiz.id);
    expect(reviews.map((r) => r.question_no)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    for (const r of reviews) {
      expect(r.is_test).toBe(true);
      expect(r.rating).toBe(r.correct ? "good" : "again");
    }
    const right = reviews.filter((r) => r.correct).length;
    await expect(page.getByTestId("quiz-score")).toHaveText(`${right} of 10`);
    // Re-opening a finished quiz shows its score, not a new one.
    await page.goto("/arsenal/quiz");
    await expect(page.getByTestId("quiz-score")).toHaveText(`${right} of 10`);
  });
});
