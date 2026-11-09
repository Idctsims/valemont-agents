import { expect, test } from "@playwright/test";

import {
  asOfLabel, boardHref, cents, comboLabel, countsLine, feeCents, orderEvents, parseFilters, pickSport, startLabel,
  type BoardEvent, creditsLine, edgeCents, edgeTone, pct, tierLabel,
} from "../src/lib/betting/board";

// The Kalshi board's pure logic (Chat 8 Phase 1; db/071). No page, no server.

test.describe("filters in the URL", () => {
  test("parse and round-trip", () => {
    expect(parseFilters({})).toEqual({ sport: null, kind: null, combo: false });
    expect(parseFilters({ sport: "Basketball", kind: "props", combo: "1" }))
      .toEqual({ sport: "Basketball", kind: "prop", combo: true });
    expect(parseFilters({ kind: "lines" }).kind).toBe("game_line");
    expect(parseFilters({ kind: "parlays", combo: "yes" })).toEqual({ sport: null, kind: null, combo: false });
    expect(parseFilters({ sport: ["Soccer", "Hockey"] }).sport).toBe("Soccer");
    for (const f of [{ sport: "Basketball", kind: "prop", combo: true }, { sport: null, kind: "game_line", combo: false },
                     { sport: "Ice Hockey", kind: null, combo: true }] as const) {
      const url = new URL(boardHref(f), "http://x");
      expect(parseFilters(Object.fromEntries(url.searchParams))).toEqual(f);
    }
    expect(boardHref({ sport: null, kind: null, combo: false })).toBe("/betting");
  });

  test("the sport shown: the URL's when on the board, else the busiest", () => {
    const chips = [{ sport: "Football", games: 120, markets: 13000 }, { sport: "Basketball", games: 40, markets: 11000 }];
    expect(pickSport(chips, "Basketball")).toBe("Basketball");
    expect(pickSport(chips, "Cricket")).toBe("Football");
    expect(pickSport(chips, null)).toBe("Football");
    expect(pickSport([], "Football")).toBeNull();
  });
});

test.describe("formatting", () => {
  test("cents and the fee", () => {
    expect(cents(0.61)).toBe("61¢");
    expect(cents(0.015)).toBe("1.5¢");
    expect(cents(0)).toBe("0¢");
    expect(cents(undefined)).toBe("—");
    expect(feeCents(0.016317)).toBe("1.63¢");
    expect(feeCents(0.0035)).toBe("0.35¢");
    expect(feeCents(null)).toBe("—");
  });

  test("times in Chicago", () => {
    expect(startLabel("2026-11-08T00:30:00Z")).toBe("Sat 6:30 PM");
    expect(asOfLabel("2026-11-07T21:30:00Z")).toBe("as of 3:30 PM");
  });

  test("the counts line", () => {
    expect(countsLine({ l: 12, p: 340, o: 6 })).toBe("12 lines · 340 props · 6 other");
    expect(countsLine({ l: 1 })).toBe("1 line");
    expect(countsLine({ p: 1 })).toBe("1 prop");
    expect(countsLine({})).toBe("No markets");
    // db/072: scalar markets (ladders, escalators) are counted apart, last.
    expect(countsLine({ l: 2, p: 3, s: 1 })).toBe("2 lines · 3 props · 1 scalar");
    expect(countsLine({ s: 20 })).toBe("20 scalar");
  });
});

test("the combo chip: YES only and the per-event leg limit; absent is never 0 or 1", () => {
  expect(comboLabel({})).toBe("Combo");
  expect(comboLabel({ y: true })).toBe("Combo · YES only");
  expect(comboLabel({ y: true, sm: 1 })).toBe("Combo · YES only · max 1");
  expect(comboLabel({ sm: 0 })).toBe("Combo · max 0");
  expect(comboLabel({ sm: undefined })).toBe("Combo");
});

test("events: game lines first, then props, then other", () => {
  const ev = (e: string, sr: string, k: "game_line" | "prop" | "other"): BoardEvent =>
    ({ e, sr, asof: "2026-11-07T15:30:00Z", m: [{ k }] });
  const out = orderEvents([ev("3", "Correct Score", "other"), ev("2", "Player Points", "prop"),
                           ev("1", "Spread", "game_line"), ev("0", "Game", "game_line")]);
  expect(out.map((e) => e.e)).toEqual(["0", "1", "2", "3"]);
});

test.describe("consensus (db/073)", () => {
  test("the tier chip names its source", () => {
    expect(tierLabel({ t: "b", n: 9 })).toBe("Books · 9");
    expect(tierLabel({ t: "e", n: 1 })).toBe("ESPN · 1 book");
    expect(tierLabel({ t: "k" })).toBe("Kalshi only");
  });

  test("p and edge read as people say them", () => {
    expect(pct(0.515282)).toBe("51.5%");
    expect(pct(undefined)).toBe("—");
    expect(edgeCents(0.047957)).toBe("+4.8¢");
    expect(edgeCents(-0.09253)).toBe("−9.3¢");
    expect(edgeCents(0.00004)).toBe("0.0¢");
    expect(edgeCents(null)).toBe("—");
  });

  test("only a claimed positive edge is up; a kalshi-tier positive stays neutral", () => {
    expect(edgeTone({ e: 0.048, ec: true })).toBe("up");
    expect(edgeTone({ e: 0.0025 })).toBe("flat");
    expect(edgeTone({ e: -0.09, ec: true })).toBe("flat");
    expect(edgeTone({ e: 0 })).toBe("flat");
  });

  test("the credits line: used of 500, and what the account has left", () => {
    expect(creditsLine({ spent: 9, allowance: 500, remaining: 480, calls: 5 }))
      .toBe("Odds API credits: 9 / 500 this month · 480 left on the account");
    expect(creditsLine({ spent: 0, allowance: 500, remaining: null, calls: 0 }))
      .toBe("Odds API credits: 0 / 500 this month");
  });
});
