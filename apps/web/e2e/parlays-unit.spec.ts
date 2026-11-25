import { expect, test } from "@playwright/test";

import {
  asStatus, asTier, dayOption, filterCounts, matchesStatus, menuDays,
  emptySlots, legLive, legWords, multText, multiplier, normName, perDollar, signedUsd, sortTickets, stage, statNow,
  tally, usd, type GameLive, type ParlayGame, type ParlayLeg, type ParlayTicket,
} from "../src/lib/betting/parlays";

// The Parlays page's pure logic (Chat 8 Phase 4.5; docs/parlays_page.md): legs
// in plain words, the live count, the money, the tickets' order. No page, no server.

const NBA: ParlayGame = {
  lg: "nba", st: "live",
  away: { abbr: "DET", loc: "Detroit", nick: "Pistons", color: "c8102e" },
  home: { abbr: "ORL", loc: "Orlando", nick: "Magic", color: "0077c0" },
};
const prop = (o: Partial<ParlayLeg>): ParlayLeg => ({
  side: "yes", label: "Jalen Suggs: 1+ threes (DET Pistons at ORL Magic)", kind: "prop", who: "Jalen Suggs",
  stat: "threes", line: 0.5, team: "ORL", sd: "home", g: 1, start: 1000, ...o,
});
const live = (players: GameLive["players"], o: Partial<GameLive> = {}): GameLive => ({
  state: "in", detail: "5:48 - 3rd", away: { score: 70, lines: [20, 25, 25] }, home: { score: 68, lines: [22, 24, 22] },
  players, ...o,
});

test.describe("legs in plain words", () => {
  test("a prop says N+ for YES and N-1 or fewer for NO, never a ticker", () => {
    expect(legWords(prop({}), NBA)).toEqual({ title: "Jalen Suggs", detail: "1+ threes", game: "Detroit at Orlando" });
    expect(legWords(prop({ line: 3.5 }), NBA).detail).toBe("4+ threes");
    expect(legWords(prop({ side: "no", line: 3.5 }), NBA).detail).toBe("3 or fewer threes");
    expect(legWords(prop({ side: "no", line: 1.5 }), NBA).detail).toBe("1 or fewer threes");
    expect(legWords(prop({ side: "no", line: 0.5 }), NBA).detail).toBe("No threes");
    expect(legWords(prop({ stat: "points", line: 14.5 }), NBA).detail).toBe("15+ pts");
    expect(legWords(prop({ stat: "combined points, rebounds, and assists", line: 24.5 }), NBA).detail).toBe("25+ pts + reb + ast");
    expect(legWords(prop({ stat: "receiving yards (protected)", line: 59.5 }), NBA).detail).toBe("60+ rec yds");
    expect(legWords(prop({ side: "no", stat: "assists", line: 0.5 }), NBA).detail).toBe("No assists");
    expect(legWords(prop({ side: "no", stat: "first goalscorer", line: undefined }), NBA).detail).toBe("Not the first goal");
    expect(legWords(prop({ stat: "double double", line: undefined }), NBA).detail).toBe("Double-double");
  });

  test("game lines read as the outcome; a NO winner with no draw is the other side winning", () => {
    const winner: ParlayLeg = { side: "yes", label: "Orlando wins (DET Pistons at ORL Magic)", kind: "game_line",
                                who: "Orlando", stat: "winner", team: "ORL", sd: "home", g: 1 };
    expect(legWords(winner, NBA).title).toBe("Orlando wins");
    expect(legWords({ ...winner, side: "no" }, NBA).title).toBe("Detroit beats Orlando");
    expect(legWords({ ...winner, side: "no" }, { ...NBA, lg: "epl" }).title).toBe("Orlando doesn't win");
    const tie: ParlayLeg = { side: "no", label: "Tie wins (Norwich vs QPR)", kind: "game_line", who: "Tie", stat: "winner" };
    expect(legWords(tie).title).toBe("No draw");
    expect(legWords({ ...winner, stat: "spread", line: 4.5 }, NBA).title).toBe("Orlando wins by 5+");
    expect(legWords({ ...winner, stat: "spread", line: 4.5, per: "2H" }, NBA).title).toBe("2nd half: Orlando wins by 5+");
    expect(legWords({ ...winner, side: "no", stat: "spread", line: 8.5 }, NBA).title).toBe("Orlando doesn't win by 9+");
    const total: ParlayLeg = { side: "yes", label: "Over 190.5 points (DET Pistons at ORL Magic)", kind: "game_line",
                               stat: "total", line: 190.5, g: 1 };
    expect(legWords(total, NBA)).toEqual({ title: "Over 190.5 pts", detail: "Detroit at Orlando" });
    expect(legWords({ ...total, side: "no" }, NBA).title).toBe("Under 190.5 pts");
    expect(legWords({ ...winner, stat: "team_total", line: 95.5, who: "Orlando" }, NBA).title).toBe("Orlando over 95.5 pts");
  });

  test("anything unknown is Kalshi's title with its side; the game comes from the label without a link", () => {
    const odd: ParlayLeg = { side: "no", label: "Feyenoord wins 1st Half wins (Feyenoord vs Porto)" };
    expect(legWords(odd)).toEqual({ title: "No: Feyenoord wins 1st Half wins", detail: "Feyenoord vs Porto" });
    expect(legWords({ side: "yes", label: "Karl Poling wins (Fradkin vs Poling)" }).title).toBe("Karl Poling wins");
  });

  test("names match across accents, punctuation and suffixes", () => {
    expect(normName("Egor Dëmin")).toBe(normName("Egor Demin"));
    expect(normName("Michael Porter Jr.")).toBe("michael porter");
    expect(normName("Nikola Vučević")).toBe("nikola vucevic");
    expect(normName("De'Aaron Fox")).toBe("deaaron fox");
  });
});

test.describe("the live count", () => {
  const suggs = (stats: Record<string, string>) => ({ [normName("Jalen Suggs")]: { side: "home" as const, stats } });

  test("a YES clears the moment the count reaches the line", () => {
    expect(legLive(prop({ line: 2.5 }), NBA, live(suggs({ ":3PT": "1-4" })), 2000))
      .toEqual({ state: "live", now: 1, target: 3, note: "5:48 - 3rd · needs 2" });
    expect(legLive(prop({ line: 2.5 }), NBA, live(suggs({ ":3PT": "3-6" })), 2000).state).toBe("cleared");
    expect(legLive(prop({ line: 2.5 }), NBA, live(suggs({ ":3PT": "2-6" }), { state: "post" }), 2000))
      .toMatchObject({ state: "busted", note: "Final · short by 1" });
  });

  test("a NO busts at the line, warns one short of it, and clears at the final", () => {
    const no = prop({ side: "no", line: 3.5 });
    expect(legLive(no, NBA, live(suggs({ ":3PT": "1-3" })), 2000)).toMatchObject({ state: "live", now: 1, target: 4, under: true, note: "5:48 - 3rd · room for 2" });
    expect(legLive(no, NBA, live(suggs({ ":3PT": "3-5" })), 2000)).toMatchObject({ state: "danger", note: "5:48 - 3rd · one more busts it" });
    expect(legLive(no, NBA, live(suggs({ ":3PT": "4-5" })), 2000).state).toBe("busted");
    expect(legLive(no, NBA, live(suggs({ ":3PT": "3-5" }), { state: "post" }), 2000).state).toBe("cleared");
  });

  test("a settled leg shows its settlement, whatever the count says", () => {
    expect(legLive(prop({ res: 1 }), NBA, live(suggs({ ":3PT": "0-4" })), 2000).state).toBe("hit");
    expect(legLive(prop({ res: 0 }), NBA, undefined, 2000).state).toBe("miss");
    expect(legLive(prop({ res: 0.5 }), NBA, undefined, 2000).state).toBe("settled");
  });

  test("before the game: its start; no player in the box score: the clock only", () => {
    expect(legLive(prop({ start: 5000 }), NBA, undefined, 2000).state).toBe("pre");
    expect(legLive(prop({ start: 1000, g: undefined }), undefined, undefined, 2000)).toEqual({ state: "live", note: "Under way · no live count" });
    expect(legLive(prop({}), NBA, live({}), 2000)).toEqual({ state: "live", note: "5:48 - 3rd" });
  });

  test("game lines count the score, a period line its periods", () => {
    const total: ParlayLeg = { side: "yes", label: "x", kind: "game_line", stat: "total", line: 190.5, g: 1 };
    expect(legLive(total, NBA, live({}), 2000)).toMatchObject({ now: 138, target: 191, state: "live" });
    expect(legLive({ ...total, per: "1H", line: 90.5 }, NBA, live({}), 2000)).toMatchObject({ now: 91, state: "cleared" });
    const winner: ParlayLeg = { side: "yes", label: "x", kind: "game_line", stat: "winner", who: "Orlando", team: "ORL", sd: "home", g: 1 };
    expect(legLive(winner, NBA, live({}), 2000)).toEqual({ state: "danger", note: "5:48 - 3rd · trails by 2" });
    expect(legLive({ ...winner, side: "no" }, NBA, live({}), 2000)).toEqual({ state: "live", note: "5:48 - 3rd · trails by 2" });
    expect(legLive(winner, NBA, live({}, { state: "post" }), 2000)).toEqual({ state: "busted", note: "Final · lost by 2" });
    const spread: ParlayLeg = { ...winner, stat: "spread", line: 1.5, sd: "away", who: "Detroit", team: "DET" };
    expect(legLive(spread, NBA, live({}), 2000)).toEqual({ state: "live", note: "5:48 - 3rd · leads by 2" });
  });

  test("ESPN's box-score keys per sport", () => {
    expect(statNow("combined points, rebounds, and assists", { ":PTS": "20", ":REB": "7", ":AST": "5" })).toBe(32);
    expect(statNow("points", { "forwards:G": "1", "forwards:A": "2" })).toBe(3);
    expect(statNow("goals", { "skaters:G": "2" })).toBe(2);
    expect(statNow("receiving yards (protected)", { "receiving:YDS": "88" })).toBe(88);
    expect(statNow("touchdowns", { "rushing:TD": "1" })).toBe(1);
    expect(statNow("passing attempts", { "passing:C/ATT": "21/33" })).toBe(33);
    expect(statNow("passing completions", { "passing:C/ATT": "21/33" })).toBeUndefined();
    expect(statNow("points leader", { ":PTS": "30" })).toBeUndefined();
  });
});

test.describe("money and the day", () => {
  const t = (o: Partial<ParlayTicket>): ParlayTicket => ({
    id: 1, tier: "safer", scope: "sport", sport: "Basketball", rv: "P2", slot: "10:45", at: 100, stake: 41.34, pays: 100,
    legs: [prop({ start: 5000 })], ...o,
  });

  test("the multiplier is the payout over the paper stake; no cents anywhere", () => {
    expect(multiplier(t({}))).toBeCloseTo(2.419, 3);
    expect(multText(t({}))).toBe("×2.42");
    expect(multText(t({ stake: 0.97 }))).toBe("×103");
    expect(usd(41.34)).toBe("$41.34");
    expect(usd(100)).toBe("$100");
    expect(signedUsd(-41.34)).toBe("−$41.34");
    expect(signedUsd(58.66)).toBe("+$58.66");
    expect(perDollar(-0.2615)).toBe("−$0.26 per $1 staked");
    for (const s of [usd(3.18), signedUsd(-3.18), multText(t({ stake: 3.18 }))]) expect(s).not.toMatch(/¢/);
  });

  test("stage, tally and the order: tiers, per-sport before cross-sport", () => {
    expect(stage(t({}), 2000)).toBe("pre");
    expect(stage(t({ legs: [prop({ start: 1000 })] }), 2000)).toBe("live");
    expect(stage(t({ outcome: "miss" }), 2000)).toBe("done");
    expect(tally([{ state: "hit" }, { state: "cleared" }, { state: "busted" }, { state: "live" }])).toEqual({ cleared: 2, out: 1 });
    const order = sortTickets([t({ id: 3, tier: "lotto", scope: "cross_sport" }), t({ id: 2, tier: "lotto" }),
                               t({ id: 4, tier: "mid" }), t({ id: 1, tier: "safer" })]).map((x) => x.id);
    expect(order).toEqual([1, 4, 2, 3]);
  });

  test("empty slots: sports with no eligible market fold into one line per slot and tier", () => {
    const { rows, noMarket } = emptySlots([
      { slot: "10:45", tier: "safer", sport: "Chess", rule: "no eligible market" },
      { slot: "10:45", tier: "safer", sport: "Esports", rule: "no eligible market" },
      { slot: "10:45", tier: "safer", sport: "Football", rule: "unmodeled pair", raw: "unmodeled pair" },
    ]);
    expect(rows.map((r) => r.sport)).toEqual(["Football"]);
    expect(noMarket.get("10:45 safer")).toEqual(["Chess", "Esports"]);
  });
});

test.describe("filters and the day menu (owner, Nov 25)", () => {
  const t = (tier: ParlayTicket["tier"], outcome?: ParlayTicket["outcome"]) => ({ tier, outcome });
  const day = [t("safer", "hit"), t("safer"), t("mid", "miss"), t("mid", "partial"), t("lotto", "miss"), t("lotto")];

  test("Open is no outcome yet; a partial shows under All only", () => {
    expect(day.filter((x) => matchesStatus(x, "open"))).toHaveLength(2);
    expect(day.filter((x) => matchesStatus(x, "hit"))).toHaveLength(1);
    expect(day.filter((x) => matchesStatus(x, "miss"))).toHaveLength(2);
    expect(day.filter((x) => matchesStatus(x, "all"))).toHaveLength(6);
  });

  test("each bar counts with the other bar's choice applied", () => {
    expect(filterCounts(day, "all", "all")).toEqual({
      tiers: { all: 6, safer: 2, mid: 2, lotto: 2 }, statuses: { all: 6, open: 2, hit: 1, miss: 2 } });
    expect(filterCounts(day, "mid", "miss")).toEqual({
      tiers: { all: 2, safer: 0, mid: 1, lotto: 1 }, statuses: { all: 2, open: 0, hit: 0, miss: 1 } });
  });

  test("a bad URL value falls back to All", () => {
    expect(asTier("mid")).toBe("mid");
    expect(asTier("bogus")).toBe("all");
    expect(asStatus(undefined)).toBe("all");
    expect(asStatus("miss")).toBe("miss");
  });

  test("the menu's words: open and hit counts, Today by name; the shown day is always listed", () => {
    expect(dayOption({ d: "2026-11-25", n: 11, open: 11, hit: 0, miss: 0 }, "2026-11-25")).toBe("Today · 11 tickets · 11 open");
    expect(dayOption({ d: "2026-11-24", n: 20, open: 16, hit: 0, miss: 4 }, "2026-11-25")).toBe("Tue, Nov 24 · 20 tickets · 16 open · 0 hit");
    expect(dayOption({ d: "2026-11-23", n: 1, open: 0, hit: 1, miss: 0 }, "2026-11-25")).toBe("Mon, Nov 23 · 1 ticket · 1 hit");
    const days = [{ d: "2026-11-24", n: 2, open: 0, hit: 0, miss: 2 }, { d: "2026-11-21", n: 1, open: 0, hit: 1, miss: 0 }];
    expect(menuDays(days, "2026-11-24")).toBe(days);
    expect(menuDays(days, "2026-11-25").map((e) => e.d)).toEqual(["2026-11-25", "2026-11-24", "2026-11-21"]);
  });
});
