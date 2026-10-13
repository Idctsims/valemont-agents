import { expect, test } from "@playwright/test";

import { SYSTEM_PAGES } from "../src/lib/nav";
import {
  MAX_PAGE,
  SWIPE_COMMIT_PX,
  chicagoMidnight,
  hides,
  nextReaction,
  otherOutlets,
  pageFromParam,
  reasonLabel,
  relativeTime,
  swipeReaction,
  tabFromParam,
} from "../src/lib/feed/types";

// The feed's pure helpers (src/lib/feed/types.ts): no page, no server.

const NOW = Date.parse("2026-10-12T17:00:00Z");

test.describe("relative time", () => {
  test("minutes, hours, yesterday, then a date", () => {
    expect(relativeTime("2026-10-12T16:59:30Z", NOW)).toBe("now");
    expect(relativeTime("2026-10-12T16:48:00Z", NOW)).toBe("12m");
    expect(relativeTime("2026-10-12T14:00:00Z", NOW)).toBe("3h");
    expect(relativeTime("2026-10-11T12:00:00Z", NOW)).toBe("Yesterday");
    expect(relativeTime("2026-10-09T12:00:00Z", NOW)).toBe("Oct 9");
  });

  test("a future timestamp reads as now, never negative", () => {
    expect(relativeTime("2026-10-12T18:00:00Z", NOW)).toBe("now");
  });
});

test.describe("other outlets", () => {
  test("one per outlet, the lead's outlet left out, order kept", () => {
    const items = [
      { name: "Bisnow South Florida", url: "https://bisnow.example/1" },
      { name: "The Real Deal Miami", url: "https://trd.example/1" },
      { name: "The Real Deal Miami", url: "https://trd.example/2" },
      { name: "Axios Miami", url: "https://axios.example/1" },
    ];
    expect(otherOutlets("Bisnow South Florida", items)).toEqual([
      { name: "The Real Deal Miami", url: "https://trd.example/1" },
      { name: "Axios Miami", url: "https://axios.example/1" },
    ]);
  });

  test("a story carried only by its lead's outlet has none", () => {
    expect(otherOutlets("ESPN NFL", [{ name: "ESPN NFL", url: "https://espn.example/2" }])).toEqual([]);
  });
});

test.describe("page parameter", () => {
  test("only a whole number from 1 to the cap", () => {
    expect(pageFromParam(undefined)).toBe(1);
    expect(pageFromParam("2")).toBe(2);
    expect(pageFromParam(["3", "4"])).toBe(3);
    for (const bad of ["0", "-1", "2.5", "abc", String(MAX_PAGE + 1), ""]) expect(pageFromParam(bad)).toBe(1);
  });
});

test.describe("filter reasons", () => {
  const patterns = new Map([[12, "box score"], [31, "^https?://(pro\\.axios\\.com|(www\\.)?axios\\.com/pro/)"]]);
  test("rule ids become their patterns; unknown reasons stay visible", () => {
    expect(reasonLabel("title_regex:12", patterns)).toBe("Title matches “box score”");
    expect(reasonLabel("url_regex:31", patterns)).toContain("axios");
    expect(reasonLabel("drop:recap_no_storyline", patterns)).toBe("Haiku: recap no storyline");
    expect(reasonLabel("title_regex:999", patterns)).toBe("title_regex:999");
  });
});

test.describe("Chicago midnight", () => {
  test("daylight time is UTC-5, standard time UTC-6", () => {
    expect(chicagoMidnight(new Date("2026-10-12T17:00:00Z"))).toBe("2026-10-12T05:00:00.000Z");
    expect(chicagoMidnight(new Date("2026-12-12T17:00:00Z"))).toBe("2026-12-12T06:00:00.000Z");
    // 03:00 UTC on the 13th is still the 12th in Chicago.
    expect(chicagoMidnight(new Date("2026-10-13T03:00:00Z"))).toBe("2026-10-12T05:00:00.000Z");
  });
});

test("the sources page is in the system pages", () => {
  expect(SYSTEM_PAGES.map((p) => p.href)).toContain("/settings/sources");
});

test.describe("reactions and tabs (Phase 2)", () => {
  test("For you is the default tab; only ?feed=latest is Latest", () => {
    expect(tabFromParam(undefined)).toBe("for-you");
    expect(tabFromParam("latest")).toBe("latest");
    expect(tabFromParam(["latest", "x"])).toBe("latest");
    expect(tabFromParam("LATEST")).toBe("for-you");
    expect(tabFromParam("anything")).toBe("for-you");
  });

  test("neutral and not interested hide a card; love, open and undo never do", () => {
    expect(hides("neutral")).toBe(true);
    expect(hides("not_interested")).toBe(true);
    for (const r of ["love", "open", "undo", null, undefined]) expect(hides(r)).toBe(false);
  });

  test("Love toggles: a loved card's Love tap is an undo", () => {
    expect(nextReaction("love", false)).toBe("love");
    expect(nextReaction("love", true)).toBe("undo");
    expect(nextReaction("not_interested", true)).toBe("not_interested");
    expect(nextReaction("neutral", false)).toBe("neutral");
  });

  test("a swipe commits only past the commit distance, in either direction", () => {
    expect(swipeReaction(SWIPE_COMMIT_PX)).toBe("love");
    expect(swipeReaction(SWIPE_COMMIT_PX - 1)).toBeNull();
    expect(swipeReaction(-SWIPE_COMMIT_PX)).toBe("not_interested");
    expect(swipeReaction(-SWIPE_COMMIT_PX + 1)).toBeNull();
    expect(swipeReaction(0)).toBeNull();
  });

  test("the feed profile is a system page", () => {
    expect(SYSTEM_PAGES.map((p) => p.href)).toContain("/settings/feed");
  });
});