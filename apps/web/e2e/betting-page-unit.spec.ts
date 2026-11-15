import { expect, test } from "@playwright/test";

import { boardHeadline, boardHref, byDay, cardPrices, claimedFilter, parseFilters } from "../src/lib/betting/board";
import { flipLeg, legFrom, loadLegs, noRefusal, requestLegs, type ComboLeg } from "../src/lib/betting/combo";
import { cardBar, cardLines, cardTeams, cardTitle, expandTicker, logoKey, logoUrls, teamTuples } from "../src/lib/betting/teams";
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

test.describe("x / % both ways on the ticket's total", () => {
  test("a total read in x and in % is the same number: x = 1 / %, and back", () => {
    for (const legs of [[0.63, 0.17], [0.5, 0.4], [0.9, 0.9, 0.9], [0.05, 0.5], [0.63, 0.85]]) {
      const t = ticketTotal(legs)!;
      const x = Number(totalText(t, "x").replace("×", ""));
      const pct = Number(totalText(t, "pct").replace("%", "")) / 100;
      // x -> %: 1 / x is the after-fee cost; % -> x: 1 / % is what $1 pays after fee.
      expect(1 / x).toBeCloseTo(t.cost, 2);
      expect(pct).toBeCloseTo(t.cost, 3);
      // Each side is rounded for display, so back across the switch is equal within 2%.
      expect(Math.abs(1 / pct - t.pays) / t.pays).toBeLessThan(0.02);
      expect(Math.abs(x - t.pays) / t.pays).toBeLessThan(0.01);
    }
  });
});

test.describe("the slip's YES / NO flip (owner, Nov 15)", () => {
  const base = { t: "E2ETEST-X-HOM", label: "Test Home to win", game: "Test Away vs Test Home" };

  test("a tap keeps both sides' prices: YES at the ask, NO at 1 − the YES bid", () => {
    const leg = legFrom(base, "yes", { a: 0.63, b: 0.61 }, false);
    expect(leg).toEqual({ ...base, side: "yes", a: 0.63, py: 0.63, pn: 0.39 });
    expect(legFrom(base, "no", { a: 0.63, b: 0.61 }, false).a).toBe(0.39);
    expect(legFrom(base, "yes", { a: 0.63, b: 0.61 }, true).yo).toBe(true);
    expect(legFrom(base, "yes", { a: 0.63 }, false)).toEqual({ ...base, side: "yes", a: 0.63, py: 0.63 });
  });

  test("flip: YES to NO at the NO price, and back to YES at the ask", () => {
    const yes = legFrom(base, "yes", { a: 0.17, b: 0.15 }, false);
    const no = flipLeg(yes, "no");
    expect(no).toEqual({ leg: { ...yes, side: "no", a: 0.85 } });
    const back = flipLeg((no as { leg: ComboLeg }).leg, "yes");
    expect(back).toEqual({ leg: { ...yes, side: "yes", a: 0.17 } });
    expect(flipLeg(yes, "yes")).toEqual({ leg: yes });
    expect(priceText(0.85, "x")).toBe("1.18×");
    expect(priceText(0.85, "pct")).toBe("85%");
  });

  test("the YES-only refusal: an event Kalshi takes on YES only never flips to NO", () => {
    const yes = legFrom(base, "yes", { a: 0.63, b: 0.61 }, true);
    const res = flipLeg(yes, "no");
    expect(res).toEqual({ refused: "YES only: Kalshi takes this event's markets on YES in a combo" });
    expect(noRefusal(yes)).toBe("YES only: Kalshi takes this event's markets on YES in a combo");
    expect(noRefusal(legFrom(base, "yes", { a: 0.63, b: 0.61 }, false))).toBeNull();
  });

  test("no NO price (no bid, or a leg ticked before the flip existed): refused, never priced at 0 or 1", () => {
    expect(flipLeg(legFrom(base, "yes", { a: 0.63 }, false), "no")).toEqual({
      refused: "No NO price here: tap the market again to read it" });
    expect(flipLeg({ ...base, side: "yes", a: 0.63 }, "no")).toEqual({
      refused: "No NO price here: tap the market again to read it" });
    expect(legFrom(base, "yes", { a: 0.63, b: 0 }, false).pn).toBeUndefined(); // 1 − 0 is not a price
  });

  test("a NO leg totals at its NO price, one fee on the combo's price", () => {
    // HOM YES 0.63 and Test Player 20+ flipped to NO (1 − 0.15 = 0.85): 0.5355, fee 0.017412.
    const t = ticketTotal([0.63, 0.85])!;
    expect(t.price).toBeCloseTo(0.5355, 12);
    expect(t.fee).toBe(0.017412);
    expect(totalText(t, "x")).toBe("1.81×");
    expect(totalText(t, "pct")).toBe("55.3%");
  });

  test("a NO leg goes to the pricer as side no", () => {
    const no = (flipLeg(legFrom(base, "yes", { a: 0.17, b: 0.15 }, false), "no") as { leg: ComboLeg }).leg;
    expect(requestLegs([legFrom({ ...base, t: "E2ETEST-X-AWY" }, "yes", { a: 0.39, b: 0.37 }, false), no]))
      .toEqual([{ market_ticker: "E2ETEST-X-AWY", side: "yes" }, { market_ticker: "E2ETEST-X-HOM", side: "no" }]);
  });

  test("stored legs keep both sides' prices and the YES-only flag; junk is dropped, never the leg", () => {
    const raw = JSON.stringify([
      { t: "A-1", side: "no", label: "A", game: "G", a: 0.39, py: 0.63, pn: 0.39 },
      { t: "B-1", side: "yes", label: "B", game: "G", a: 0.5, py: 0.5, pn: 1.2, yo: true },
      { t: "C-1", side: "yes", label: "C", game: "G", yo: "yes" },
    ]);
    const legs = loadLegs({ getItem: () => raw });
    expect(legs[0]).toEqual({ t: "A-1", side: "no", label: "A", game: "G", a: 0.39, py: 0.63, pn: 0.39 });
    expect(legs[1]).toEqual({ t: "B-1", side: "yes", label: "B", game: "G", a: 0.5, py: 0.5, yo: true });
    expect(legs[2]).toEqual({ t: "C-1", side: "yes", label: "C", game: "G" });
  });
});

test.describe("the board's cards (db/082, db/083)", () => {
  test("each card's moneyline prices: ticker, Kalshi's code, bid and ask, in the reader's order", () => {
    expect(cardPrices({ g: "KXNBAGAME-26NOV15GSWHOU", w: [["GSW", 0.37, 0.39, 0.38], ["HOU", 0.61, 0.63, 0.62]] })).toEqual([
      { t: "KXNBAGAME-26NOV15GSWHOU-GSW", code: "GSW", b: 0.37, a: 0.39 },
      { t: "KXNBAGAME-26NOV15GSWHOU-HOU", code: "HOU", b: 0.61, a: 0.63 },
    ]);
    // A tie last, an unquoted side kept without a price, a single-market event's own ticker.
    expect(cardPrices({ g: "G", w: [["A", 0.4, 0.42, 0.4], ["B", null, null, null], ["TIE", 0.2, 0.22, 0.2]] }).map((p) => [p.t, p.a]))
      .toEqual([["G-A", 0.42], ["G-B", undefined], ["G-TIE", 0.22]]);
    expect(cardPrices({ g: "G", w: [["", 0.5, 0.52, 0.51]] })[0].t).toBe("G");
    expect(cardPrices({ g: "G" })).toEqual([]);
  });

  test("the Claimed edges filter keeps only games with hc", () => {
    const cards = [{ g: "a", hc: true }, { g: "b" }, { g: "c", hc: true }];
    expect(claimedFilter(cards, true).map((c) => c.g)).toEqual(["a", "c"]);
    expect(claimedFilter(cards, false).map((c) => c.g)).toEqual(["a", "b", "c"]);
  });

  test("the filter rides in the URL with the sport", () => {
    expect(boardHref({ sport: "Basketball", kind: null, combo: false, claimed: true })).toBe("/betting?sport=Basketball&claimed=1");
    expect(boardHref({ sport: "Basketball", kind: null, combo: false, claimed: false })).toBe("/betting?sport=Basketball");
    expect(parseFilters({ sport: "Basketball", claimed: "1" }).claimed).toBe(true);
    expect(parseFilters({ sport: "Basketball", claimed: "yes" }).claimed).toBeUndefined();
  });
});

test.describe("the mock's card: teams, logos, bar, main lines (db/083)", () => {
  const nba = {
    g: "KXNBAGAME-26NOV13MILORL", at: 1794600000,
    w: [["MIL", 0.23, 0.24, 0.231], ["ORL", 0.77, 0.78, 0.769]] as [string, number, number, number][],
    wn: 9,
    tm: [["Milwaukee", "Bucks", "nba/mil", "00471b"], ["Orlando", "Magic", "nba/orl", "0077c0"]] as
      [string, string | null, string | null, string | null][],
    sp: ["KXNBASPREAD-~-ORL8", "ORL", 8.5, 0.52, 0.53, 1] as [string, string, number, number, number, number],
    to: ["KXNBATOTAL-~-224", 224.5, 0.51, 0.52, 0] as [string, number, number, number, number],
  };

  test("ESPN logo keys become ESPN's URLs; a dark variant only where ESPN keeps one", () => {
    expect(logoUrls("nba/det")).toEqual({ logo: "https://a.espncdn.com/i/teamlogos/nba/500/det.png",
                                          logoDark: "https://a.espncdn.com/i/teamlogos/nba/500-dark/det.png" });
    expect(logoUrls("ncaa/254")).toEqual({ logo: "https://a.espncdn.com/i/teamlogos/ncaa/500/254.png",
                                           logoDark: "https://a.espncdn.com/i/teamlogos/ncaa/500/254.png" });
    for (const bad of [null, undefined, "", "../x", "nba/det.png", "NBA/det", "nba/det/x"]) expect(logoUrls(bad)).toEqual({});
    expect(logoKey("nba/500/scoreboard/det.png")).toBe("nba/det");
    expect(logoKey("ncaa/500/254.png")).toBe("ncaa/254");
    expect(logoKey("https://example.com/x.png")).toBeNull();
  });

  test("teams in Kalshi's order: city, nickname, logo, colour, Kalshi's code, home or away", () => {
    const [a, h] = cardTeams(nba)!;
    expect(a).toEqual({ city: "Milwaukee", nick: "Bucks", logo: "https://a.espncdn.com/i/teamlogos/nba/500/mil.png",
                        logoDark: "https://a.espncdn.com/i/teamlogos/nba/500-dark/mil.png", color: "#00471b", code: "MIL",
                        home: false });
    expect(h.home).toBe(true);
    // A swapped link: home first.
    const sw = cardTeams({ ...nba, hs: true })!;
    expect(sw.map((t) => t.home)).toEqual([true, false]);
    // Soccer's club name as city and nickname: said once.
    expect(cardTeams({ ...nba, tm: [["Brighton & Hove Albion", "Brighton & Hove Albion", "soccer/331", "0606fa"],
                                    ["AEK Athens", "AEK Athens", null, null]] })!.map((t) => t.nick)).toEqual([undefined, undefined]);
    // A colour that is not six hex digits is no colour.
    expect(cardTeams({ ...nba, tm: [["X", null, null, "red"], ["Y", null, null, null]] })!.map((t) => t.color)).toEqual([undefined, undefined]);
  });

  test("no ESPN link: the sides as the title writes them, with Kalshi's codes", () => {
    expect(cardTeams({ t: "BC Neptunas Klaipeda at BC Zalgiris Kaunas", w: [["NEP", 0.1, 0.11, 0.1], ["ZAL", 0.89, 0.9, 0.9]] }))
      .toEqual([{ city: "BC Neptunas Klaipeda", code: "NEP", home: false }, { city: "BC Zalgiris Kaunas", code: "ZAL", home: true }]);
    expect(cardTeams({ t: "Tournament winner" })).toBeNull();
    expect(cardTitle({ ...nba })).toBe("Milwaukee Bucks at Orlando Magic");
    expect(cardTitle({ ...nba, t: "MIL Bucks at ORL Magic" })).toBe("MIL Bucks at ORL Magic");
  });

  test("the bar: each side's p and whose number it is", () => {
    expect(cardBar(nba)).toEqual({ p: [0.231, 0.769], source: "9 books say" });
    expect(cardBar({ ...nba, wn: undefined, we: true })!.source).toBe("ESPN's book says");
    expect(cardBar({ ...nba, wn: undefined })!.source).toBe("Kalshi mid");
    expect(cardBar({ w: [["A", 0.4, 0.42, null], ["B", 0.5, 0.52, null]] })).toBeNull();
  });

  test("the Spread and Total cells: the team's city and the line, the full ticker back from '~'", () => {
    const { spread, total } = cardLines(nba);
    expect(spread).toEqual({ t: "KXNBASPREAD-26NOV13MILORL-ORL8", label: "Orlando −8.5", b: 0.52, a: 0.53, yesOnly: true });
    expect(total).toEqual({ t: "KXNBATOTAL-26NOV13MILORL-224", label: "Over 224.5", b: 0.51, a: 0.52, yesOnly: false });
    expect(expandTicker("KXNBASPREAD-OTHER-ORL8", nba.g)).toBe("KXNBASPREAD-OTHER-ORL8");
    expect(cardLines({ g: "G" })).toEqual({});
  });

  test("the game page reads the linked ESPN row into the same teams", () => {
    expect(teamTuples({ participants: { away: { name: "Milwaukee Bucks", loc: "Milwaukee", nick: "Bucks",
                                                logo: "nba/500/scoreboard/mil.png", color: "00471b" },
                                        home: { name: "Orlando Magic" } },
                        home: "Orlando Magic", away: "Milwaukee Bucks", home_away_swapped: false }))
      .toEqual({ tm: [["Milwaukee", "Bucks", "nba/mil", "00471b"], ["Orlando Magic", null, null, null]] });
    expect(teamTuples({ participants: { away: { name: "A" }, home: { name: "H" } }, home: "H", away: "A", home_away_swapped: true }))
      .toEqual({ tm: [["H", null, null, null], ["A", null, null, null]], hs: true });
    expect(teamTuples({ participants: [{ name: "x" }], home: null, away: null, home_away_swapped: null })).toBeNull();
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
