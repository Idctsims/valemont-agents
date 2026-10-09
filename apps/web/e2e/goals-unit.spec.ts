// Goals and navigation logic, no browser and no server: the period maths
// (must agree with db/021), the week strip, the cap message, and the nav
// registry's "only built pages render" rule. Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import {
  addDays,
  isoWeek,
  isoWeekLabel,
  localToday,
  longDate,
  monthStart,
  periodFor,
  weekRange,
  weekStart,
} from "../src/lib/goals/period";
import { holdsSlot, ordered, overCapMessage, segments, type Goal } from "../src/lib/goals/types";
import { NAV, SYSTEM_PAGES, builtGroups, isActive } from "../src/lib/nav";

function goal(over: Partial<Goal>): Goal {
  return {
    id: crypto.randomUUID(),
    title: "g",
    notes: null,
    horizon: "weekly",
    area: null,
    period_start: "2026-10-05",
    status: "open",
    carried_from: null,
    carry_count: 0,
    sort_order: 0,
    created_at: "2026-10-05T12:00:00Z",
    completed_at: null,
    moved: false,
    ...over,
  };
}

test.describe("periods", () => {
  test("week starts on Monday, month on the 1st, long term has none", () => {
    expect(weekStart("2026-10-09")).toBe("2026-10-05"); // Friday
    expect(weekStart("2026-10-05")).toBe("2026-10-05"); // Monday
    expect(weekStart("2026-10-11")).toBe("2026-10-05"); // Sunday
    expect(weekStart("2026-11-01")).toBe("2026-10-26"); // Sunday the 1st
    expect(monthStart("2026-10-09")).toBe("2026-10-01");
    expect(periodFor("weekly", "2026-10-09")).toBe("2026-10-05");
    expect(periodFor("monthly", "2026-10-09")).toBe("2026-10-01");
    expect(periodFor("long_term", "2026-10-09")).toBeNull();
    expect(addDays("2026-12-28", 7)).toBe("2027-01-04");
  });

  test("local today is the owner's date, not UTC's", () => {
    // 04:30 UTC on the 12th is still the 11th in Chicago (CDT, UTC-5).
    expect(localToday(new Date("2026-10-12T04:30:00Z"))).toBe("2026-10-11");
    expect(localToday(new Date("2026-10-12T05:30:00Z"))).toBe("2026-10-12");
    // After DST ends (CST, UTC-6).
    expect(localToday(new Date("2026-11-02T05:30:00Z"))).toBe("2026-11-01");
  });

  test("ISO weeks, including the year boundary", () => {
    expect(isoWeek("2026-10-09")).toEqual({ year: 2026, week: 41 });
    expect(isoWeekLabel("2026-01-01")).toBe("2026-W01"); // Thursday
    expect(isoWeekLabel("2027-01-01")).toBe("2026-W53"); // Friday: 2026 has 53 weeks
    expect(isoWeekLabel("2021-01-03")).toBe("2020-W53");
  });

  test("labels", () => {
    expect(longDate("2026-10-09")).toBe("Friday, October 9");
    expect(weekRange("2026-10-05")).toBe("Oct 5 – 11");
    expect(weekRange("2026-09-28")).toBe("Sep 28 – Oct 4");
  });
});

test.describe("the week strip and the cap", () => {
  test("ten slots: done, then open, then unused", () => {
    const s = segments([goal({ status: "open" }), goal({ status: "done" }), goal({ status: "dropped" })], "live");
    expect(s).toEqual(["done", "open", ...Array(8).fill("empty")]);
  });

  test("a goal moved on frees its slot", () => {
    const moved = goal({ moved: true });
    expect(holdsSlot(moved)).toBe(false);
    expect(segments([moved], "live")).toEqual(Array(10).fill("empty"));
  });

  test("past ten the strip grows and the warning appears at eleven", () => {
    const eleven = Array.from({ length: 11 }, () => goal({}));
    expect(segments(eleven, "live")).toHaveLength(11);
    expect(overCapMessage(10)).toBeNull();
    expect(overCapMessage(11)).toBe("11 of 10. Something's not getting done.");
  });

  test("history: done filled, carried outlined, dropped hollow", () => {
    const s = segments(
      [goal({ status: "dropped" }), goal({ moved: true }), goal({ status: "done" })],
      "history",
    );
    expect(s.slice(0, 3)).toEqual(["done", "carried", "dropped"]);
  });

  test("rows order open, done, dropped, moved", () => {
    const list = ordered([
      goal({ title: "moved", moved: true }),
      goal({ title: "dropped", status: "dropped" }),
      goal({ title: "done", status: "done" }),
      goal({ title: "open" }),
    ]);
    expect(list.map((g) => g.title)).toEqual(["open", "done", "dropped", "moved"]);
  });
});

test.describe("navigation registry", () => {
  test("holds all sixteen pillars in the brief's five groups", () => {
    expect(NAV.map((g) => g.name)).toEqual(["Command", "Culture", "Tech", "Sports", "Crypto"]);
    expect(NAV.flatMap((g) => g.pillars.map((p) => p.n))).toEqual(
      Array.from({ length: 16 }, (_, i) => i + 1),
    );
  });

  test("only built pages render: today, Home and Goals", () => {
    expect(builtGroups()).toEqual([
      {
        key: "command",
        name: "Command",
        pages: [
          { label: "Home", href: "/" },
          { label: "Goals", href: "/goals" },
        ],
      },
    ]);
    const unbuilt = NAV.flatMap((g) => g.pillars.filter((p) => !p.built).map((p) => p.href));
    const shown = builtGroups().flatMap((g) => g.pages.map((p) => p.href));
    expect(unbuilt.filter((h) => shown.includes(h))).toEqual([]);
    expect(SYSTEM_PAGES.map((p) => p.href)).toEqual(["/settings/health", "/onboarding", "/design"]);
  });

  test("/ is active only on itself", () => {
    expect(isActive("/", "/")).toBe(true);
    expect(isActive("/goals", "/")).toBe(false);
    expect(isActive("/goals", "/goals")).toBe(true);
    expect(isActive("/settings/health", "/settings")).toBe(true);
  });
});
