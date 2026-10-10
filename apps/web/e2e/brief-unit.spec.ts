// The Morning Brief's web helpers, no browser and no server: the label time
// in the owner's zone, the late flag, latest-row-wins per date in the
// history, and the Brief pillar for Wags. Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import { briefDay, briefTime, isLate, latestPerDate, type Brief } from "../src/lib/brief/types";
import { pageSlice, parsePageContext } from "../src/lib/wags/page-context";

const b = (id: number, date: string, createdAt = "2026-10-11T11:15:00Z"): Brief => ({
  id,
  date,
  createdAt,
  body: { headline: `h${id}`, sections: [], one_move: "m" },
  fallback: false,
  model: "claude-sonnet-5-5",
  wordCount: 120,
  isTest: false,
});

test("the label shows the owner's local time", () => {
  // 11:15 UTC on Oct 11 is 6:15 AM in Chicago (CDT).
  expect(briefTime("2026-10-11T11:15:00Z")).toBe("6:15 AM");
  expect(briefDay("2026-10-11")).toBe("Sun, Oct 11");
});

test("a brief written after 9:45 AM Chicago is late", () => {
  expect(isLate("2026-10-11T14:45:00Z")).toBe(false); // 9:45
  expect(isLate("2026-10-11T14:46:00Z")).toBe(true); // 9:46
  expect(isLate("2026-10-11T11:15:00Z")).toBe(false);
});

test("history keeps the latest row per date, newest date first", () => {
  const out = latestPerDate([b(1, "2026-10-11"), b(3, "2026-10-12"), b(2, "2026-10-11")]);
  expect(out.map((x) => [x.date, x.id])).toEqual([
    ["2026-10-12", 3],
    ["2026-10-11", 2],
  ]);
});

test("Ask Wags about this sends the brief's slice", () => {
  const ctx = parsePageContext({ route: "/brief", label: "Brief", pillar: "brief" });
  expect(ctx).toEqual({ route: "/brief", label: "Brief", pillar: "brief" });
  expect(pageSlice({ brief: { headline: "H", one_move: "M" } }, ctx!)).toEqual({ headline: "H", one_move: "M" });
});
