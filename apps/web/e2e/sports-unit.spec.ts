import { expect, test } from "@playwright/test";

import { builtGroups } from "../src/lib/nav";
import { SPORT_CHIPS, feedHref, sportFromParam } from "../src/lib/feed/types";

// Sports News' pure helpers (Chat 3 Phase 3): the chips, their URL state, and
// that Home's feed URLs are exactly Phase 2's. No page, no server.

test.describe("sport chips", () => {
  test("All, CFB, NFL, NBA, F1, Golf, in that order", () => {
    expect(SPORT_CHIPS.map((c) => c.label)).toEqual(["All", "CFB", "NFL", "NBA", "F1", "Golf"]);
    expect(SPORT_CHIPS.map((c) => c.sport)).toEqual([null, "cfb", "nfl", "nba", "f1", "golf"]);
  });

  test("?sport= is a chip; anything else is All", () => {
    expect(sportFromParam("golf")).toBe("golf");
    expect(sportFromParam(["f1", "nba"])).toBe("f1");
    for (const raw of [undefined, "", "GOLF", "mlb", "none", ["x"]]) expect(sportFromParam(raw)).toBeNull();
  });
});

test.describe("feed URLs", () => {
  test("Home's are Phase 2's, unchanged", () => {
    expect(feedHref("/", { tab: "for-you" })).toBe("/#stories");
    expect(feedHref("/", { tab: "latest" })).toBe("/?feed=latest#stories");
    expect(feedHref("/", { tab: "latest", page: 2 })).toBe("/?feed=latest&latest=2#stories");
    expect(feedHref("/", { tab: "for-you", page: 3 })).toBe("/?foryou=3#stories");
  });

  test("Sports keeps the chip through tabs and pages", () => {
    expect(feedHref("/sports", { tab: "for-you", sport: "golf" })).toBe("/sports?sport=golf#stories");
    expect(feedHref("/sports", { tab: "latest", sport: "golf", page: 2 })).toBe("/sports?sport=golf&feed=latest&latest=2#stories");
    expect(feedHref("/sports", { tab: "latest", sport: null, anchor: false })).toBe("/sports?feed=latest");
    expect(feedHref("/sports", { tab: "for-you", sport: null, anchor: false })).toBe("/sports");
  });
});

test.describe("navigation", () => {
  test("Sports News is built, at /sports", () => {
    const sports = builtGroups().find((g) => g.key === "sports");
    expect(sports?.pages.map((p) => [p.label, p.href])).toEqual([["Sports News", "/sports"]]);
  });
});
