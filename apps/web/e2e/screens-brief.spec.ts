import { expect, test, type Page } from "@playwright/test";

import { addTestBrief, chicagoDay, hasOwner, setTheme, signIn, useE2eMarker } from "./support/owner";

// Screenshot review for Chat 2 Phase 5: Home with the Morning Brief, and
// /brief, in both themes. Phone 390x844: Home top, /brief top and bottom.
// Desktop 1440: Home and /brief full page. Written to
// e2e/screenshots/chat2-p5/ (gitignored).
//
// The brief is a TEST brief (db/030 add_test_brief, the e2e marker), so it
// carries the "test" and "plain mode" tags in the shots; the real views
// never show it.
//   pnpm test:e2e e2e/screens-brief.spec.ts --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p5";

const BODY = {
  headline: "Permits decide the week; the DEP filing is four days late",
  one_move: "File the DEP application before noon, then call the lender.",
  sections: [
    { key: "goals", title: "Goals", lines: ["Goals: 2 of 8 done, with three days left.", "The SBC permit has carried twice. Third time is a pattern."] },
    { key: "ventures", title: "Ventures", lines: ["Sail Beach Club: DEP filing, overdue since Oct 7.", "Sail Beach Club: lender call due today.", "Gravity Club next: pick a renderer."] },
    { key: "capital", title: "Capital", lines: ["Paper: $1,000.00, flat on the day."] },
  ],
};

async function shot(page: Page, name: string, phone: boolean, theme: string, bottom = false) {
  const w = phone ? "390" : "1440";
  if (phone) {
    await page.evaluate((b) => window.scrollTo(0, b ? document.documentElement.scrollHeight : 0), bottom);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${DIR}/${name}${bottom ? "-bottom" : "-top"}-${w}-${theme}.png` });
  } else {
    await page.screenshot({ path: `${DIR}/${name}-${w}-${theme}.png`, fullPage: true });
  }
}

test("brief screens", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  if (phone) await addTestBrief(chicagoDay(), BODY);
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  await signIn(page, "/");

  for (const theme of ["night", "day"] as const) {
    await setTheme(page, theme);
    await page.goto("/");
    await expect(page.getByTestId("brief-headline")).toHaveText(BODY.headline);
    await shot(page, "home-brief", phone, theme);

    await page.goto("/brief");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(BODY.headline);
    await shot(page, "brief", phone, theme);
    if (phone) await shot(page, "brief", phone, theme, true);
  }
});
