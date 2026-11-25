import { expect, test } from "@playwright/test";

import { GAME_TICKER, gameFromTitle, liveFromKalshi } from "../src/lib/betting/kalshi-live-parse";
import { legKey, legLive, type ParlayLeg } from "../src/lib/betting/parlays";

// Kalshi's soccer live data on the Parlays page (owner, Nov 25), on the shapes
// probed that day: PSG vs Roma at halftime (scorers, the score by half), Stoke vs
// Millwall on the Genius feed (no half split, no scorers), a finished Opta game.
// No page, no server.

const PSG = {
  away_same_game_score: 1, home_same_game_score: 1, half: "Halftime", status: "live", status_text: "Halftime",
  time: "45'", widget_status: "live", match_status: "halftime",
  period_scores: [{ away_score: 1, home_score: 1, number: 1, type: "regular_period" }],
  home_significant_events: [{ event_type: "score_change", player: "Illia Zabarnyi", time: "31'" }],
  away_significant_events: [{ event_type: "score_change", player: "Mario Hermoso", time: "5'" },
                            { event_type: "yellow_card", player: "Gianluca Mancini", time: "41'" }],
};
const STOKE_LIVE = {
  home_same_game_score: 2, away_same_game_score: 0, half: "2nd half", status: "live", status_text: "2nd half",
  time: "67'", widget_status: "live", provider: "genius", clock_running: true,
};
const FINAL = {
  home_same_game_score: 2, away_same_game_score: 4, half: "FT", status: "closed", status_text: "Full-Time",
  time: "90'", widget_status: "finished", winner: "away", provider: "opta",
};

const leg = (o: Partial<ParlayLeg>): ParlayLeg => ({ side: "yes", label: "x", gk: "KXUCLGAME-26NOV25PSGROM", start: 1, ...o });

test("a game ticker, the title's sides (home first), the key", () => {
  expect(GAME_TICKER.test("KXEFLCHAMPIONSHIPGAME-26NOV25STKMIL")).toBe(true);
  expect(GAME_TICKER.test("KXAFCCL2GAME-26NOV25MUHARK13BA")).toBe(true);
  expect(GAME_TICKER.test("kxucl'; drop")).toBe(false);
  expect(gameFromTitle("PSG vs Roma")).toEqual({ lg: "soccer", home: { name: "PSG" }, away: { name: "Roma" } });
  expect(gameFromTitle("no teams here")).toBeUndefined();
  expect(legKey({ g: 12 })).toBe("12");
  expect(legKey({ gk: "KXUCLGAME-26NOV25PSGROM" })).toBe("k:KXUCLGAME-26NOV25PSGROM");
  expect(legKey({})).toBeUndefined();
});

test("live: the score, Halftime, the score by half, the named scorers only", () => {
  const live = liveFromKalshi(PSG, "PSG vs Roma", new Set(["mario hermoso"]));
  expect(live).toMatchObject({ state: "in", detail: "Halftime", home: { score: 1, lines: [1] }, away: { score: 1, lines: [1] } });
  expect(live.players).toEqual({ "mario hermoso": { side: "away", stats: { ":G": "1" } } });
  const genius = liveFromKalshi(STOKE_LIVE, "Stoke vs Millwall", new Set());
  expect(genius).toMatchObject({ state: "in", detail: "67'", home: { score: 2 }, away: { score: 0 } });
  expect(genius.home?.lines).toBeUndefined();
  expect(liveFromKalshi(FINAL, "A vs B", new Set())).toMatchObject({ state: "post", home: { score: 2 }, away: { score: 4 } });
});

test("legs count from it: a total, a winner by name, a first-half line, a scorer", () => {
  const live = liveFromKalshi(PSG, "PSG vs Roma", new Set(["mario hermoso"]));
  const g = live.game;
  const under = legLive(leg({ side: "no", kind: "game_line", stat: "total", line: 3.5 }), g, live, 100);
  expect(under).toMatchObject({ state: "live", now: 2, target: 4, note: "Halftime · room for 1" });
  const psg = legLive(leg({ kind: "game_line", stat: "winner", who: "PSG" }), g, live, 100);
  expect(psg).toMatchObject({ state: "danger", note: "Halftime · tied" });
  const half = legLive(leg({ kind: "game_line", stat: "total", line: 1.5, per: "1H" }), g, live, 100);
  expect(half).toMatchObject({ state: "cleared", now: 2 });
  const scorer = legLive(leg({ kind: "prop", who: "Mario Hermoso", stat: "goals", line: 0.5 }), g, live, 100);
  expect(scorer).toMatchObject({ state: "cleared", now: 1, target: 1 });
  // Stoke 2-0 on the Genius feed: no half split, so a first-half line shows the clock, never a count.
  const stoke = liveFromKalshi(STOKE_LIVE, "Stoke vs Millwall", new Set());
  const h1 = legLive(leg({ gk: "KXEFLCHAMPIONSHIPGAME-26NOV25STKMIL", kind: "game_line", stat: "total", line: 0.5, per: "1H" }),
                     stoke.game, stoke, 100);
  expect(h1).toEqual({ state: "live", note: "67'" });
  const won = legLive(leg({ gk: "KXX-1", kind: "game_line", stat: "winner", who: "B" }), gameFromTitle("A vs B"),
                      liveFromKalshi(FINAL, "A vs B", new Set()), 100);
  expect(won).toMatchObject({ state: "cleared", note: "Final · won by 2" });
});

test("no live data: still under way with no count", () => {
  expect(legLive(leg({ kind: "game_line", stat: "total", line: 2.5 }), undefined, undefined, 100))
    .toEqual({ state: "live", note: "Under way · no live count" });
});
