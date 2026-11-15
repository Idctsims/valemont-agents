import { expect, test } from "@playwright/test";

import { boardHeadline, byDay } from "../src/lib/betting/board";
import { loadLegs } from "../src/lib/betting/combo";
import {
  OTHER_STAT, boxTitle, eventTag, isLadder, isWinnerEvent, mainIndex, marketTicker, orderBoxes, periodLabel,
  periodsOf, playerRows, rungLabel, rungWindow, sidesOf, singleLine, statLabel, tabKey, winnerConsensus,
  type TabEvent, type TabMarket,
} from "../src/lib/betting/game";
import {
  multiplier, parseUnit, percent, priceText, sideAsk, takerFee, ticketTotal, totalText,
} from "../src/lib/betting/price";

// The new /betting's pure logic (Chat 8 Phase 3.5, Workstream B): the x / %
// switch, the ticket's after-fee total, and how db/079's tabs become boxes,
// ladders and player rows. No page, no server.

test.describe("x / %", () => {
  test("the stored choice: x unless % was chosen", () => {
    expect(parseUnit(null)).toBe("x");
    expect(parseUnit("pct")).toBe("pct");
    expect(parseUnit("x")).toBe("x");
    expect(parseUnit("percent")).toBe("x");
  });

  test("a price as what $1 pays (before fee) or as the chance it implies", () => {
    expect(priceText(0.48, "x")).toBe("2.08×");
    expect(priceText(0.5, "x")).toBe("2.00×");
    expect(priceText(0.08, "x")).toBe("12.5×");
    expect(priceText(0.01, "x")).toBe("100.0×");
    expect(priceText(0.61, "pct")).toBe("61%");
    expect(priceText(0.015, "pct")).toBe("1.5%");
    expect(priceText(0.999, "pct")).toBe("99.9%");
  });

  test("every price flips both ways, and back", () => {
    for (const p of [0.01, 0.05, 0.17, 0.33, 0.5, 0.63, 0.9, 0.99]) {
      const x = Number(priceText(p, "x").replace("×", ""));
      expect(1 / x).toBeCloseTo(p, 2);
      expect(Number(priceText(p, "pct").replace("%", "")) / 100).toBeCloseTo(p, 3);
    }
  });

  test("no usable price is a dash, never Infinity or 0×", () => {
    for (const p of [undefined, null, 0, 1, -0.2, 1.5, Number.NaN]) {
      expect(priceText(p as number | undefined, "x")).toBe("—");
      expect(priceText(p as number | undefined, "pct")).toBe("—");
    }
  });

  test("multiplier and percent formats", () => {
    expect(multiplier(9.994)).toBe("9.99×");
    expect(multiplier(10)).toBe("10.0×");
    expect(percent(0.1946)).toBe("19.5%");
    expect(percent(0.2)).toBe("20%");
  });

  test("a side's price: YES at the ask, NO at 1 − the YES bid", () => {
    expect(sideAsk({ a: 0.63, b: 0.61 }, "yes")).toBe(0.63);
    expect(sideAsk({ a: 0.63, b: 0.61 }, "no")).toBe(0.39);
    expect(sideAsk({ a: 0.63 }, "no")).toBeUndefined();
    expect(sideAsk({ b: 0 }, "no")).toBeUndefined();
  });
});

test.describe("the ticket's total, after fee", () => {
  test("Kalshi's taker fee at a price (quadratic, rounded up at $0.000001)", () => {
    expect(takerFee(0.63)).toBe(0.016317); // db/073's figure for the test board's home side
    expect(takerFee(0.39)).toBe(0.016653);
    expect(takerFee(0.5)).toBe(0.0175);
    expect(takerFee(0.1071)).toBe(0.006695); // 0.0066940713 rounds UP
  });

  test("one fee on the combo's price, never one per leg", () => {
    const t = ticketTotal([0.5, 0.4])!;
    expect(t.price).toBeCloseTo(0.2, 12);
    expect(t.fee).toBe(0.0112);
    expect(t.cost).toBeCloseTo(0.2112, 12);
    expect(t.pays).toBeCloseTo(1 / 0.2112, 12);
    expect(totalText(t, "x")).toBe("4.73×");
    expect(totalText(t, "pct")).toBe("21.1%");
    // Fee per leg (Phase 3's naive cost) would charge more: (0.5 + 0.0175) × (0.4 + 0.0168).
    expect((0.5 + takerFee(0.5)) * (0.4 + takerFee(0.4))).toBeGreaterThan(t.cost);
  });

  test("after fee always pays less than before fee", () => {
    for (const legs of [[0.63, 0.17], [0.9, 0.9, 0.9], [0.05, 0.5], [0.99, 0.99]]) {
      const t = ticketTotal(legs)!;
      expect(t.pays).toBeLessThan(1 / t.price);
      expect(t.fee).toBeGreaterThan(0);
    }
  });

  test("the e2e ticket: Test Home YES 0.63 and Test Player 20+ 0.17", () => {
    const t = ticketTotal([0.63, 0.17])!;
    expect(t.price).toBeCloseTo(0.1071, 12);
    expect(t.fee).toBe(0.006695);
    expect(totalText(t, "x")).toBe("8.79×");
    expect(totalText(t, "pct")).toBe("11.4%");
    expect(priceText(t.price, "x")).toBe("9.34×"); // the combo's price before fee
    // Both moneylines (the refusal case): 0.63 × 0.39 = 0.2457, fee 0.012974.
    const both = ticketTotal([0.63, 0.39])!;
    expect(both.fee).toBe(0.012974);
    expect(totalText(both, "x")).toBe("3.87×");
  });

  test("fewer than 2 legs, or a leg with no price: no total", () => {
    expect(ticketTotal([])).toBeNull();
    expect(ticketTotal([0.5])).toBeNull();
    expect(ticketTotal([0.5, undefined])).toBeNull();
    expect(ticketTotal([0.5, 1])).toBeNull();
    expect(totalText(null, "x")).toBe("—");
  });

  test("stored legs keep their price; a junk price is dropped, never the leg", () => {
    const raw = JSON.stringify([
      { t: "A-1", side: "yes", label: "A", game: "G", a: 0.42 },
      { t: "B-1", side: "yes", label: "B", game: "G", a: 7 },
      { t: "C-1", side: "no", label: "C", game: "G" },
    ]);
    const legs = loadLegs({ getItem: () => raw });
    expect(legs.map((l) => [l.t, l.a])).toEqual([["A-1", 0.42], ["B-1", undefined], ["C-1", undefined]]);
    expect("a" in legs[1]).toBe(false);
  });
});

test.describe("the board", () => {
  test("cards by Chicago day, in start order", () => {
    const g = (at: string, id: string) => ({ at, g: id });
    const days = byDay([g("2026-11-16T01:00:00Z", "late"), g("2026-11-15T18:00:00Z", "noon"), g("2026-11-16T18:00:00Z", "mon")]);
    expect(days.map((d) => [d.day, d.games.map((x) => x.g)])).toEqual([
      ["Sunday, November 15", ["noon", "late"]], // 01:00 UTC Mon is 7 PM Sunday in Chicago
      ["Monday, November 16", ["mon"]],
    ]);
  });

  test("the headline counts today's games, else what is ahead", () => {
    const now = new Date("2026-11-15T15:00:00Z");
    expect(boardHeadline([{ at: "2026-11-15T18:00:00Z" }, { at: "2026-11-16T01:00:00Z" }], now))
      .toEqual({ lead: "Today's", n: "two", tail: "games." });
    expect(boardHeadline([{ at: "2026-11-16T18:00:00Z" }], now)).toEqual({ lead: "", n: "one", tail: "game ahead." });
    expect(boardHeadline(Array.from({ length: 16 }, () => ({ at: "2026-11-15T18:00:00Z" })), now).n).toBe("16");
  });
});

const ev = (e: string, m: TabMarket[], extra: Partial<TabEvent> = {}): TabEvent =>
  ({ e, sr: e.split("-")[0], asof: "2026-11-15T15:00:00Z", m, ...extra });

test.describe("the game page", () => {
  test("the sides as the title writes them", () => {
    expect(sidesOf("KC Chiefs at ATL Falcons")).toEqual({ a: { code: "KC", name: "Chiefs" }, h: { code: "ATL", name: "Falcons" } });
    expect(sidesOf("LA Rams at ARI Cardinals")?.h).toEqual({ code: "ARI", name: "Cardinals" });
    expect(sidesOf("Test Away vs Test Home")).toEqual({ a: { code: "Test", name: "Away" }, h: { code: "Test", name: "Home" } });
    expect(sidesOf("Sinner")).toBeNull();
  });

  test("a market's ticker from its event and suffix", () => {
    expect(marketTicker({ e: "KXNFLGAME-26NOV15SFDAL" }, { x: "DAL" })).toBe("KXNFLGAME-26NOV15SFDAL-DAL");
    expect(marketTicker({ e: "KXPGAPLAYOFF-AUCPBCS26" }, { x: "" })).toBe("KXPGAPLAYOFF-AUCPBCS26");
  });

  test("box titles: the league's prefix off; two events of one series told apart", () => {
    const g = "KXNFLGAME-26NOV15SFDAL";
    expect(eventTag("KXNFLRACE-26NOV15SFDAL20", g)).toBe("20");
    expect(eventTag("KXNFLSPREAD-26NOV15SFDAL", g)).toBeNull();
    expect(eventTag("E2ETEST-ABCDPTS", "E2ETEST-ABCD")).toBe("PTS");
    expect(boxTitle({ e: "KXNFLRACE-26NOV15SFDAL20", sr: "KXNFLRACE", t: "Pro Football Race to Points" }, g, true))
      .toBe("Race to Points · 20");
    expect(boxTitle({ e: "KXNFLSPREAD-26NOV15SFDAL", sr: "KXNFLSPREAD", t: "Pro Football Spread" }, g, false)).toBe("Spread");
    expect(boxTitle({ e: "X-1", sr: "X" }, g, false)).toBe("X");
  });

  test("the moneyline event rides in the header", () => {
    expect(isWinnerEvent(ev("G", [{ x: "DAL", st: "winner", sj: "Dallas" }, { x: "SF", st: "winner" }]))).toBe(true);
    expect(isWinnerEvent(ev("S", [{ x: "DAL2", st: "spread", ln: 1.5 }]))).toBe(false);
    expect(isWinnerEvent(ev("E", []))).toBe(false);
  });

  test("the header's consensus: whose number and each side's p", () => {
    expect(winnerConsensus(ev("G", [{ x: "A", q: "b3b6482", a: 0.63 }, { x: "B", q: "ki380-26", a: 0.39, b: 0.37 }])))
      .toEqual({ source: "3 books say", p: [0.648, 0.38] });
    expect(winnerConsensus(ev("G", [{ x: "B", q: "ki380-26", a: 0.39, b: 0.37 }, { x: "A", q: "b3b6482", a: 0.63 }])).source)
      .toBe("3 books say");
    expect(winnerConsensus(ev("G", [{ x: "A" }, { x: "B" }]))).toEqual({ source: "Kalshi prices", p: [null, null] });
    expect(winnerConsensus(ev("G", [{ x: "A", q: "ec346-20" }])).source).toBe("ESPN's book says");
  });

  test("rungs read as the mock writes them", () => {
    expect(rungLabel({ x: "a", ln: 24.5, sj: "Victor Wembanyama" }, "players")).toBe("25+");
    expect(rungLabel({ x: "a", ln: 0.5, sj: "Player" }, "players")).toBe("1+");
    expect(rungLabel({ x: "a", ln: 1.5, st: "spread", lb: "Dallas wins by over 1.5 points" }, "lines")).toBe("−1.5");
    expect(rungLabel({ x: "a", ln: 34.5, st: "total", lb: "Over 34.5 points" }, "lines")).toBe("34.5");
    expect(rungLabel({ x: "a", ln: 1.5, sj: "Dallas: 2+" }, "lines")).toBe("2+");
    expect(rungLabel({ x: "a", ln: 14, sj: "14+ points" }, "lines")).toBe("14+");
    expect(rungLabel({ x: "a", sj: "Dallas by 1 to 6" }, "lines")).toBe("Dallas by 1 to 6");
  });

  test("a ladder needs two lines; the main rung is the books' line, else the one nearest 50¢", () => {
    expect(isLadder([{ x: "1", ln: 1.5 }, { x: "2", ln: 2.5 }])).toBe(true);
    expect(isLadder([{ x: "1", ln: 1.5 }, { x: "2", ln: 1.5 }])).toBe(false);
    expect(isLadder([{ x: "1" }, { x: "2" }])).toBe(false);
    expect(isLadder([{ x: "1", ln: 1.5, s: true }, { x: "2", ln: 2.5 }])).toBe(false);
    expect(mainIndex([{ x: "1", a: 0.5, q: "kc500-1" }, { x: "2", a: 0.2, q: "b5a200-1" }])).toBe(1);
    expect(mainIndex([{ x: "1", a: 0.9 }, { x: "2", a: 0.55 }, { x: "3", a: 0.3 }])).toBe(1);
    expect(mainIndex([])).toBe(0);
  });

  test("nine rungs around the main line until All lines", () => {
    expect(rungWindow(10, 3)).toEqual([0, 10]);
    expect(rungWindow(20, 10)).toEqual([6, 15]);
    expect(rungWindow(20, 0)).toEqual([0, 9]);
    expect(rungWindow(20, 19)).toEqual([11, 20]);
    expect(rungWindow(152, 76)).toEqual([72, 81]);
  });

  test("boxes: spreads, totals, team totals first, then by title", () => {
    const out = orderBoxes([
      ev("Z-1", [{ x: "a" }], { t: "Safety" }), ev("T-1", [{ x: "a", st: "team_total", ln: 3 }], { t: "Team Total" }),
      ev("A-1", [{ x: "a" }], { t: "Both Teams" }), ev("S-1", [{ x: "a", st: "spread", ln: 1 }], { t: "Spread" }),
      ev("O-1", [{ x: "a", st: "total", ln: 40 }], { t: "Total" }),
    ]);
    expect(out.map((e) => e.t)).toEqual(["Spread", "Total", "Team Total", "Both Teams", "Safety"]);
  });

  test("players by team, ranked by where the market puts them; rungs by line", () => {
    const e = ev("KXNBAPTS-X", [
      { x: "B10", sj: "Bench", tm: "HOU", ln: 9.5, a: 0.4 },
      { x: "S30", sj: "Star", tm: "HOU", ln: 29.5, a: 0.3 }, { x: "S25", sj: "Star", tm: "HOU", ln: 24.5, a: 0.55 },
      { x: "R20", sj: "Rival", tm: "GSW", ln: 19.5, a: 0.6 },
    ]);
    const rows = playerRows([e]);
    expect([...rows.keys()]).toEqual(["HOU", "GSW"]);
    expect(rows.get("HOU")!.map((r) => r.name)).toEqual(["Star", "Bench"]);
    expect(rows.get("HOU")![0].markets.map((m) => m.ln)).toEqual([24.5, 29.5]);
    expect(rows.get("HOU")![0].markets[0].ev.e).toBe("KXNBAPTS-X");
    expect(singleLine(rows.get("GSW")!)).toBe(true);
    expect(singleLine(rows.get("HOU")!)).toBe(false);
  });

  test("periods in game order, and their words", () => {
    expect(periodsOf([{ pd: "2Q" }, { pd: "1H" }, {}, { pd: "1Q" }, { pd: "1H" }])).toEqual(["1H", "1Q", "2Q"]);
    expect(periodLabel("1H")).toBe("1st half");
    expect(periodLabel("3Q")).toBe("3rd quarter");
    expect(periodLabel("2P")).toBe("2nd period");
    expect(periodLabel("4Q")).toBe("4th quarter");
  });

  test("stat chips, and Other as the empty stat", () => {
    expect(statLabel("receiving yards (protected)")).toBe("Receiving yards · protected");
    expect(statLabel("points")).toBe("Points");
    expect(statLabel(OTHER_STAT)).toBe("Other");
    expect(tabKey("players", "points")).toBe("players|points");
    expect(tabKey("players", OTHER_STAT)).toBe("players|");
    expect(tabKey("lines", "points")).toBe("lines");
  });
});
