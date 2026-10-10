import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { RUN_ID, deleteWagsThreads, hasOwner, setTheme, signIn, ventureSlugNamed } from "./support/owner";

// Screenshot review for Chat 2 Phase 4: Wags in both themes, on the MOCK
// model (playwright.config.ts runs the server with WAGS_MOCK=1; the footer
// must say "Mock" before anything is sent, or the spec stops).
//   Phone 390x844: the sheet empty (starters, context chip), mid-conversation
//   with a proposal row, and the budget line in budget mode.
//   Desktop 1440: the right-side panel over a venture page.
// Written to e2e/screenshots/chat2-p4/ (gitignored). Only this spec's threads
// are touched, and they are deleted by id at the end; no proposal is
// confirmed, so nothing else is written.
//   pnpm test:e2e e2e/screens-wags.spec.ts --workers=1

test.skip(!hasOwner, "set E2E_OWNER_EMAIL and E2E_OWNER_PASSWORD to run");

const DIR = "e2e/screenshots/chat2-p4";
const P = `e2e ${RUN_ID} `;

async function openSheet(page: Page, phone: boolean) {
  await page.getByTestId(phone ? "dock-wags" : "rail-wags").click();
  const sheet = page.getByTestId("wags-sheet");
  await expect(sheet.getByRole("textbox", { name: "Message Wags" })).toBeVisible();
  await expect(sheet.getByTestId("wags-status")).toHaveText(/^(Mock|Haiku · budget mode) · /);
  return sheet;
}

/**
 * The sheet must own the bottom edge: it reaches the viewport's bottom, and
 * what is drawn at the bottom-left corner belongs to it, not to the page or
 * the dock behind it. (The first Chat 2 Phase 4 shots showed something
 * clipped under the footer.)
 */
async function sheetOwnsTheBottom(page: Page) {
  const probe = await page.evaluate(() => {
    const sheet = document.querySelector('[data-testid="wags-sheet"]')!;
    const r = sheet.getBoundingClientRect();
    const at = document.elementFromPoint(40, innerHeight - 2);
    return {
      bottom: Math.round(r.bottom),
      viewport: innerHeight,
      inside: !!at && sheet.contains(at),
      at: at ? `${at.tagName}.${String(at.className).slice(0, 60)}` : "none",
    };
  });
  expect(probe.bottom, JSON.stringify(probe)).toBeGreaterThanOrEqual(probe.viewport);
  expect(probe.inside, `bottom-left belongs to ${probe.at}`).toBe(true);
}

/** Every visible element touching the bottom 12px, written next to the shots. */
async function probeBottom(page: Page, name: string) {
  const probe = await page.evaluate(() => {
    const h = innerHeight;
    const hits = [...document.querySelectorAll("body *")]
      .map((el) => ({ el, r: el.getBoundingClientRect(), cs: getComputedStyle(el) }))
      .filter(({ r, cs }) => r.height > 0 && r.width > 0 && r.bottom > h - 12 && r.top < h + 40 && cs.visibility !== "hidden" && cs.opacity !== "0" && cs.display !== "none")
      .map(({ el, r, cs }) => ({
        tag: el.tagName,
        cls: String(el.getAttribute("class") ?? "").slice(0, 90),
        testid: el.getAttribute("data-testid"),
        text: (el.childNodes.length && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim()) ? el.textContent!.trim().slice(0, 50) : ""),
        top: Math.round(r.top),
        bottom: Math.round(r.bottom),
        left: Math.round(r.left),
        z: cs.zIndex,
        pos: cs.position,
      }));
    return {
      innerHeight: h,
      visual: window.visualViewport ? [Math.round(window.visualViewport.width), Math.round(window.visualViewport.height)] : null,
      scroll: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      hits,
    };
  });
  writeFileSync(`${DIR}/probe-${name}.json`, JSON.stringify(probe, null, 2));
}

async function send(page: Page, text: string) {
  const box = page.getByTestId("wags-sheet").getByRole("textbox", { name: "Message Wags" });
  await box.fill(text);
  await box.press("Enter");
  await expect(page.getByTestId("wags-sheet").getByTestId("wags-turn").last()).not.toHaveText(/Reading your pillars/);
  await expect(page.getByTestId("wags-sheet").getByTestId("wags-streaming")).toHaveCount(0);
}

test("wags screens", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const phone = testInfo.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  const w = phone ? "390" : "1440";
  const threads: string[] = [];
  const slug = (await ventureSlugNamed("Sail Beach Club")) ?? "";
  test.skip(!slug, "no venture named Sail Beach Club");

  await signIn(page, `/ventures/${slug}`);
  try {
    for (const theme of ["night", "day"] as const) {
      await setTheme(page, theme);
      await page.goto(`/ventures/${slug}`);
      await page.evaluate(() => sessionStorage.removeItem("vm-wags-thread"));

      // 1. Empty: starters and the context chip.
      let sheet = await openSheet(page, phone);
      await expect(sheet.getByTestId("wags-starters")).toBeVisible();
      await expect(sheet.getByTestId("wags-context-chip")).toHaveText("Context · Ventures · Sail Beach Club");
      if (phone) await sheetOwnsTheBottom(page);
      await page.screenshot({ path: `${DIR}/wags-empty-${w}-${theme}.png` });

      // 2. Mid-conversation, with a proposal waiting for an answer.
      await send(page, `${P}What should I focus on today?`);
      await send(page, `propose goal: ${P}Sign the SBC permit application`);
      await expect(sheet.getByTestId("wags-proposal").last()).toHaveAttribute("data-state", "pending");
      const id = await page.evaluate(() => sessionStorage.getItem("vm-wags-thread"));
      if (id) threads.push(id);
      if (phone) {
        mkdirSync(DIR, { recursive: true });
        await probeBottom(page, `proposal-${theme}`);
        await sheetOwnsTheBottom(page);
      }
      await page.screenshot({ path: `${DIR}/wags-proposal-${w}-${theme}.png` });

      // 3. Budget mode (forced for this request; honoured in mock mode only).
      if (phone) {
        await page.route("**/api/wags", (route) =>
          route.continue({ headers: { ...route.request().headers(), "x-wags-e2e-budget": "downgrade" } }),
        );
        await send(page, `${P}Summarize my week`);
        await expect(sheet.getByTestId("wags-status")).toContainText("Haiku · budget mode");
        await page.unroute("**/api/wags");
        await page.screenshot({ path: `${DIR}/wags-budget-${w}-${theme}.png` });
      }

      await sheet.getByRole("button", { name: "Close", exact: true }).click();
      sheet = page.getByTestId("wags-sheet");
      await expect(sheet).toHaveCount(0);
    }
  } finally {
    await deleteWagsThreads(threads);
  }
});
