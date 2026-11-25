// Kalshi's soccer live data as the Parlays page's live state (kalshi-live.ts
// fetches; this only reads). Pure: no fetch, no server. Shapes probed Nov 25 on
// three feeds (Genius, Opta, one unnamed).

import { normName, type GameLive, type GameSide, type ParlayGame, type PlayerLive } from "./parlays";

export type Details = Record<string, unknown>;

/** A Kalshi game event ticker: KX<series>-<date and teams>. */
export const GAME_TICKER = /^KX[A-Z0-9]{2,60}-[A-Z0-9-]{4,80}$/;

const int = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : undefined);

/** "Stoke vs Millwall" -> the two sides as a parlay game (home first, as Kalshi titles soccer). */
export function gameFromTitle(title: string): ParlayGame | undefined {
  const m = /^(.+?)\s+vs\.?\s+(.+)$/.exec(title.trim());
  if (!m) return undefined;
  return { lg: "soccer", home: { name: m[1] }, away: { name: m[2] } };
}

/** One soccer milestone's live data as the page's live state. */
export function liveFromKalshi(d: Details, title: string, names: Set<string>): GameLive {
  const status = String(d.widget_status ?? d.status ?? "");
  const state: GameLive["state"] = status === "live" ? "in" : status === "finished" || d.status === "closed" ? "post" : "pre";
  const half = String(d.half ?? d.status_text ?? "");
  const time = typeof d.time === "string" ? d.time : "";
  const detail = state === "in" ? (/half ?time/i.test(half) ? "Halftime" : time || String(d.status_text ?? "Live")) : undefined;
  const lines: Record<GameSide, number[] | undefined> = { home: undefined, away: undefined };
  if (Array.isArray(d.period_scores)) {
    const regular = (d.period_scores as { number?: unknown; type?: unknown; home_score?: unknown; away_score?: unknown }[])
      .filter((p) => p.type === "regular_period" && typeof p.number === "number")
      .sort((a, b) => (a.number as number) - (b.number as number));
    if (regular.length) {
      lines.home = regular.map((p) => int(p.home_score) ?? 0);
      lines.away = regular.map((p) => int(p.away_score) ?? 0);
    }
  }
  // Goal scorers, for anytime / first goal props (":G" is the box-score key statNow reads).
  const players: Record<string, PlayerLive> = {};
  for (const side of ["home", "away"] as const) {
    const evs = d[`${side}_significant_events`];
    if (!Array.isArray(evs)) continue;
    for (const e of evs as { event_type?: unknown; player?: unknown }[]) {
      if (e.event_type !== "score_change" || typeof e.player !== "string") continue;
      const key = normName(e.player);
      if (!names.has(key)) continue;
      const p = (players[key] ??= { side, stats: { ":G": "0" } });
      p.stats![":G"] = String(Number(p.stats![":G"]) + 1);
    }
  }
  return {
    state, detail,
    home: { score: int(d.home_same_game_score), lines: lines.home },
    away: { score: int(d.away_same_game_score), lines: lines.away },
    players, game: gameFromTitle(title),
  };
}

