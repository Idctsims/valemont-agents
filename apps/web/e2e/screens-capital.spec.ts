import { expect, test, type Page } from "@playwright/test";

import { money } from "../src/lib/capital/types";

import { RUN_ID, addTestEntry, hasOwner, setTheme, signIn, snapshotCapital, useE2eMarker } from "./support/owner";

// Screenshot review for Chat 2 Phase 3: /capital in both themes, first as it
// stands, then with a deposit and its correcting adjustment applied. Phone:
// viewport shots at 390x844, at load and scrolled to the bottom. Desktop:
// 1440 full page. Written to e2e/screenshots/chat2-p3/ (gitignored).
//
// The deposit and its correction are TEST entries (db/027, written with the
// e2e marker): excluded from the real record, shown (tagged "test") only to a
// request carrying the marker, which this spec's requests do. The real paper
// total and every real entry are checked unchanged at the end.
//   pnpm test:e2e e2e/screens-capital.spec.ts --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p3";

async function capture(page: Page, name: string, phone: boolean, theme: string) {
  const shot = (n: string, w: string, fullPage: boolean) =>
    page.screenshot({ path: `${DIR}/${n}-${w}-${theme}.png`, fullPage });
  if (phone) {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(150);
    await shot(`${name}-top`, "390", false);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(300);
    await shot(`${name}-bottom`, "390", false);
    await page.evaluate(() => window.scrollTo(0, 0));
  } else {
    await shot(name, "1440", true);
  }
}

test("capital screens", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  const note = `e2e ${RUN_ID}`;

  const before = await snapshotCapital();
  const shown = before.paperTotal + before.testNet;
  await useE2eMarker(page.context(), testInfo.project.use.baseURL!);
  await signIn(page, "/capital");

  for (const state of ["seed", "corrected"] as const) {
    if (state === "corrected") {
      await addTestEntry("deposit", "250.00", `${note} deposit`);
      await addTestEntry("adjustment", "-250.00", `${note} correction`);
    }
    for (const theme of ["night", "day"] as const) {
      await setTheme(page, theme);
      await page.goto("/capital");
      await page.waitForLoadState("networkidle");
      await expect(page.getByTestId("capital-paper").getByTestId("capital-total")).toHaveText(money(shown));
      if (state === "corrected") {
        await expect(page.getByTestId("entry-row").filter({ hasText: `${note} correction` })).toHaveCount(1);
      }
      await capture(page, `capital-${state}`, phone, theme);
    }
  }

  const after = await snapshotCapital();
  expect(after.paperTotal).toBe(before.paperTotal);
  expect([...after.entries.keys()].sort(), "no real entry was added").toEqual([...before.entries.keys()].sort());
  for (const [id, row] of before.entries) expect(after.entries.get(id), `entry ${id} changed`).toBe(row);
});
