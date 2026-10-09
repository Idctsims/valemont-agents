import { expect, test, type Page } from "@playwright/test";

import { localToday } from "../src/lib/goals/period";

import { addTempDate, createTempVenture, deleteVenturesById, hasOwner, setTheme, signIn, snapshotVentures } from "./support/owner";

// Screenshot review for Chat 2 Phase 2: /ventures, /ventures/sail-beach-club
// (first workstream expanded, Parked fold open) and Home with TODAY showing,
// in both themes. Phone: viewport shots at 390x844, at load and scrolled to
// the bottom. Desktop: 1440 full page. Written to e2e/screenshots/chat2-p2/
// (gitignored).
//
// For TODAY it creates one temporary venture ("e2e <run id> …") with a date
// due today, by id, and deletes it by id afterwards. The owner's real
// ventures are only read, and are checked unchanged at the end.
//   pnpm test:e2e screens --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p2";

async function shot(page: Page, name: string, width: string, theme: string, fullPage: boolean) {
  await page.screenshot({ path: `${DIR}/${name}-${width}-${theme}.png`, fullPage });
}

async function capture(page: Page, name: string, phone: boolean, theme: string) {
  if (phone) {
    // Opening a workstream scrolls; the top shot is the top (hero, NEXT).
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(150);
    await shot(page, `${name}-top`, "390", theme, false);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(300);
    await shot(page, `${name}-bottom`, "390", theme, false);
    await page.evaluate(() => window.scrollTo(0, 0));
  } else {
    await shot(page, name, "1440", theme, true);
  }
}

test("ventures screens", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });

  const real = await snapshotVentures();
  const created: string[] = [];
  try {
    const temp = await createTempVenture("today", { stage: "building", next_action: "Shown in TODAY" });
    created.push(temp.id);
    // Unique per run: the two projects may run this spec at the same time.
    const label = `${temp.name} sample date due today`;
    await addTempDate(temp.id, label, localToday());

    await signIn(page, "/");
    for (const theme of ["night", "day"] as const) {
      await setTheme(page, theme);

      await page.goto("/");
      await page.waitForLoadState("networkidle");
      await expect(page.getByTestId("today-row").filter({ hasText: label })).toBeVisible();
      await capture(page, "home-today", phone, theme);

      await page.goto("/ventures");
      await page.waitForLoadState("networkidle");
      if (phone) {
        // Owner review: every real venture fits one 390x844 screen, above
        // the dock, without scrolling. (The temporary TODAY venture is not
        // the owner's and is left out of the check.)
        const dockTop = (await page.getByRole("navigation", { name: "Dock" }).boundingBox())!.y;
        const rows = page.locator('[data-testid="venture-row"]:not([data-slug^="e2e-"])');
        expect(await rows.count()).toBeGreaterThan(0);
        for (const box of await rows.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().bottom))) {
          expect(box, "a real venture row runs under the dock").toBeLessThanOrEqual(dockTop);
        }
      }
      await capture(page, "ventures", phone, theme);

      await page.goto("/ventures/sail-beach-club");
      await page.waitForLoadState("networkidle");
      await page.getByTestId("workstream-row").first().getByRole("button").first().click();
      await page.getByTestId("workstreams-parked").getByRole("button", { name: /^Parked · \d+$/ }).click();
      await page.waitForTimeout(250);
      await capture(page, "sail-beach-club", phone, theme);
    }
  } finally {
    await deleteVenturesById(created);
  }

  const after = await snapshotVentures();
  expect([...after.keys()].sort()).toEqual([...real.keys()].sort());
  for (const [key, value] of real) expect(after.get(key), `${key} changed`).toBe(value);
});
