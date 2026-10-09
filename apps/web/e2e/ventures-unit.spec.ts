// Ventures HQ helpers, no browser and no server: slugs (must satisfy
// db/022's CHECK), the date and log labels in the owner's timezone, the
// "not set up" rule, the meta line and the nearest open date.
// Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import {
  dueLabel,
  isSetUp,
  ledgerOrder,
  logStamp,
  metaLine,
  nearestOpen,
  slugify,
  type VentureDate,
} from "../src/lib/ventures/types";

const SLUG_CHECK = /^[a-z0-9]+(-[a-z0-9]+)*$/;

test("slugs match the seed and db/022's CHECK", () => {
  expect(slugify("Sail Beach Club")).toBe("sail-beach-club");
  expect(slugify("Sims & Vale Capital")).toBe("sims-vale-capital");
  expect(slugify("Freelance web dev")).toBe("freelance-web-dev");
  expect(slugify("  Café — Ünïcode!! ")).toBe("cafe-unicode");
  for (const name of ["e2e 1a2b3c venture", "A", "x".repeat(200), "--a--b--"]) {
    const s = slugify(name);
    expect(s, name).toMatch(SLUG_CHECK);
    expect(s.length).toBeLessThanOrEqual(80);
  }
});

test("due labels count days from today", () => {
  expect(dueLabel("2026-10-14", "2026-10-11")).toBe("Oct 14 · 3d");
  expect(dueLabel("2026-10-11", "2026-10-11")).toBe("Oct 11 · today");
  expect(dueLabel("2026-10-09", "2026-10-11")).toBe("Oct 9 · 2d late");
});

test("log stamps read in Chicago time, 24-hour", () => {
  // 18:42 UTC is 13:42 in Chicago (CDT).
  expect(logStamp("2026-10-09T18:42:00Z")).toBe("Oct 9 · 13:42");
  // 03:30 UTC on the 10th is still the 9th in Chicago.
  expect(logStamp("2026-10-10T03:30:00Z")).toBe("Oct 9 · 22:30");
});

test("a name-only venture is not set up", () => {
  const blank = { stage: null, next_action: null, tagline: null, role: null };
  expect(isSetUp(blank)).toBe(false);
  expect(isSetUp({ ...blank, stage: "idea" })).toBe(true);
  expect(isSetUp({ ...blank, next_action: "Call Marcus" })).toBe(true);
});

test("meta line: role and stage, whichever are set", () => {
  expect(metaLine({ role: "Co-owner & CEO", stage: "pre_launch" })).toBe("Co-owner & CEO · Pre-launch");
  expect(metaLine({ role: null, stage: "scaling" })).toBe("Scaling");
  expect(metaLine({ role: null, stage: null })).toBe("");
});

test("ledger order: set up first, then name-only, the owner's order kept within each", () => {
  const v = (name: string, stage: "idea" | null) => ({ name, stage, next_action: null, tagline: null, role: null });
  const seeded = [v("SBC", "idea"), v("PTM", null), v("Clipd", null), v("Grow", "idea"), v("Excursion", null)];
  expect(ledgerOrder(seeded).map((x) => x.name)).toEqual(["SBC", "Grow", "PTM", "Clipd", "Excursion"]);
});

test("the nearest open date ignores done ones", () => {
  const d = (id: string, due: string, done = false): VentureDate => ({
    id, venture_id: "v", workstream_id: null, label: id, due_on: due, done_at: done ? "2026-10-01T00:00:00Z" : null,
  });
  expect(nearestOpen([d("a", "2026-10-20"), d("b", "2026-10-05", true), d("c", "2026-10-12")])?.id).toBe("c");
  expect(nearestOpen([d("b", "2026-10-05", true)])).toBeNull();
});
