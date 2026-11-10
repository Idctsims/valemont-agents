import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  asOfLabel, boardHref, cents, comboLabel, countsLine, feeCents, orderEvents, parseFilters, pickSport, startLabel,
  type BoardEvent, creditsLine, edgeCents, edgeTone, parseConsensus, pct, staleLabel, tierLabel,
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

test.describe("consensus (db/073, db/074)", () => {
  test("the tier chip names its source", () => {
    expect(tierLabel({ t: "b", n: 9 })).toBe("Books · 9");
    expect(tierLabel({ t: "e", n: 1 })).toBe("ESPN · 1 book");
    expect(tierLabel({ t: "k" })).toBe("Kalshi only");
  });

  test("the packed q: YES side, claimed, fresh", () => {
    const q = parseConsensus({ q: "b3b6482", a: 0.63, b: 0.61, f: 0.016317 });
    expect(q).toEqual({ t: "b", n: 3, p: 0.648, sd: "y", e: 0.002, a: 0.63, f: 0.016317,
                        ec: true, staleK: false, staleO: false });
  });

  test("the packed q: NO side, its ask from the bid and its fee derived", () => {
    // Kalshi only, NO better (8), p 0.38: NO at 1 - 0.37 = 0.63, edge -0.026.
    const q = parseConsensus({ q: "ki380-26", a: 0.39, b: 0.37, f: 0.016653 })!;
    expect([q.t, q.n, q.sd, q.a, q.e, q.ec]).toEqual(["k", undefined, "n", 0.63, -0.026, false]);
    expect(q.f).toBeCloseTo(0.016, 6);
    expect(q.fApprox).toBe(true);
  });

  test("stale flags: tagged, never claimed", () => {
    const k = parseConsensus({ q: "b9c515-93", a: 0.5, b: 0.49, f: 0.0175 })!;   // 2: Kalshi stale
    expect([k.staleK, k.staleO, k.ec, k.sd]).toEqual([true, false, false, "y"]);
    expect(staleLabel(k)).toBe("stale Kalshi price");
    const o = parseConsensus({ q: "ee500-10", a: 0.5, b: 0.49, f: 0.0175 })!;    // 4: book stale
    expect(staleLabel(o)).toBe("stale book price");
    expect(staleLabel(parseConsensus({ q: "eg500-10" })!)).toBe("stale prices");
    expect(staleLabel(parseConsensus({ q: "ka500-10" })!)).toBeNull();
    expect(parseConsensus({ q: "zz" })).toBeNull();
    expect(parseConsensus({})).toBeNull();
  });

  test("p and edge read as people say them", () => {
    expect(pct(0.515282)).toBe("51.5%");
    expect(pct(undefined)).toBe("—");
    expect(edgeCents(0.047957)).toBe("+4.8¢");
    expect(edgeCents(-0.09253)).toBe("−9.3¢");
    expect(edgeCents(0.00004)).toBe("0.0¢");
    expect(edgeCents(null)).toBe("—");
  });

  test("only a claimed positive edge is up; a kalshi-tier or stale positive stays neutral", () => {
    expect(edgeTone({ e: 0.048, ec: true })).toBe("up");
    expect(edgeTone({ e: 0.0025, ec: false })).toBe("flat");
    expect(edgeTone({ e: -0.09, ec: true })).toBe("flat");
    expect(edgeTone({ e: 0, ec: false })).toBe("flat");
  });

  test("the credits line: the account's own count of 500, and what is left", () => {
    expect(creditsLine({ used: 20, spent: 9, allowance: 500, remaining: 480, calls: 5 }))
      .toBe("Odds API credits: 20 / 500 used this month · 480 left on the account");
    expect(creditsLine({ used: null, spent: 0, allowance: 500, remaining: null, calls: 0 }))
      .toBe("Odds API credits: 0 / 500 used this month");
  });
});

// The packed q's one decoder against the SQL encoder (db/074): every case is
// kalshi_board_game's output with the fields leg_consensus_for says it
// encodes (e2e/fixtures/consensus-packed.json: production, read-only, plus the
// scratch copy for the flags production had none of).
test.describe("the packed q round-trips the SQL encoder", () => {
  type Case = { q: string; a: number | null; b: number | null; f: number | null;
                want: { t: string; n: number | null; p: number; sd: string; e: number; ec: boolean;
                        staleK: boolean; staleO: boolean } };
  const fixture = JSON.parse(readFileSync(path.join(__dirname, "fixtures", "consensus-packed.json"), "utf8")) as
    { cases: Case[] };

  test("every tier, side and flag the encoder produced", () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(40);
    const seen = new Set<string>();
    for (const c of fixture.cases) {
      const q = parseConsensus({ q: c.q, a: c.a ?? undefined, b: c.b ?? undefined, f: c.f ?? undefined });
      expect(q, c.q).not.toBeNull();
      const w = c.want;
      expect([q!.t, q!.n ?? null, q!.sd, q!.ec, q!.staleK, q!.staleO], c.q)
        .toEqual([w.t, w.n, w.sd, w.ec, w.staleK, w.staleO]);
      expect(q!.p, c.q).toBeCloseTo(w.p, 6);
      expect(q!.e, c.q).toBeCloseTo(w.e, 6);
      if (w.sd === "y") {
        expect([q!.a, q!.f, q!.fApprox], c.q).toEqual([c.a ?? undefined, c.f ?? undefined, undefined]);
      } else if (c.b !== null) {
        expect(q!.a, c.q).toBeCloseTo(1 - c.b, 6);
        expect(q!.fApprox).toBe(true);
      }
      seen.add(`${w.t}${w.sd}${+w.ec}${+w.staleK}${+w.staleO}`);
    }
    // Claimed on both sides, and each stale flag, are in the fixture.
    for (const k of ["by100", "bn100", "ey001", "ky010"]) expect(seen, k).toContain(k);
  });
});
