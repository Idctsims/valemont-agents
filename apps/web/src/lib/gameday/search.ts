// Game Day's team search (owner, Nov 21: a magnifying glass on Game Day, a
// box that suggests teams with their logos as you type). A team is found
// through the games it plays in sports_events; tapping it opens the game
// that matters now: live, else the next, else the most recent. Pure: no I/O
// (data.ts reads the rows).

import { LEAGUES, isLive, type Status } from "./slate";
import { slateSides, type SlateSide, type TeamRow } from "./teams";

export const SEARCH_MIN = 2;
export const SEARCH_MAX_TEAMS = 12;
/** Under this many letters the text must start a word ("st" is in every "State"); from it, anywhere ("bama"). */
export const SEARCH_ANYWHERE = 4;
/** Rows read per search: "st" alone matched 492 games in 14 days on Nov 21; ~270 bytes a row. */
export const SEARCH_ROWS = 400;
/** How far back a game still counts as a team's "last" one. */
export const SEARCH_BACK_DAYS = 14;

/** One game as the search reads it: names, faces, start and status. */
export type SearchRow = TeamRow & { league: string; home: string; away: string; starts_at: string; status: Status };

export type TeamHit = {
  name: string;
  league: string;
  face: SlateSide;
  /** The game the tap opens. */
  gameId: number;
  opponent: string;
  /** Home or away in that game; "vs" when it is home, "at" when away. */
  home: boolean;
  at: string;
  status: Status;
};

/**
 * The typed text as a PostgREST ilike pattern body: letters and digits
 * kept, anything else a wildcard (so "St. John's" still finds
 * "St John's"), never a comma, parenthesis or quote that could break the
 * filter. Null when too short to search.
 */
export function searchPattern(q: string): string | null {
  const t = q.trim().replace(/\s+/g, " ");
  if (t.replace(/[^\p{L}\p{N}]/gu, "").length < SEARCH_MIN) return null;
  // Spaces too, so "ala crim" finds "Alabama Crimson Tide" (nameMatches then checks the words).
  return t.slice(0, 40).replace(/[^\p{L}\p{N}]/gu, "*").replace(/\*+/g, "*");
}

function norm(s: string): string {
  // "St. John's" reads "st johns": an apostrophe or a period never splits a word.
  return s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/['’.]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

/** Whether a team name answers the query: every typed word starts a word of the name, or the text is inside it. */
export function nameMatches(name: string, q: string): boolean {
  const n = norm(name);
  const words = norm(q).split(" ").filter(Boolean);
  if (!words.length) return false;
  const parts = n.split(" ");
  return n.includes(words.join(" ")) || words.every((w) => parts.some((p) => p.startsWith(w)));
}

/** The game a tap opens: live first, then the next not yet started, then the last one played. */
function pick(games: { row: SearchRow; home: boolean }[], now: number) {
  const live = games.find((g) => isLive(g.row));
  if (live) return live;
  const next = games.find((g) => g.row.status === "scheduled" && Date.parse(g.row.starts_at) >= now - 4 * 3_600_000);
  if (next) return next;
  return [...games].reverse().find((g) => Date.parse(g.row.starts_at) <= now) ?? games[0];
}

const ORDER = (league: string) => LEAGUES[league]?.sort ?? 999;

/** The rows' teams that answer the query, one hit each, best first. Rows must be in start order. */
export function teamHits(rows: readonly SearchRow[], q: string, now: number): TeamHit[] {
  const byTeam = new Map<string, { name: string; league: string; face: SlateSide; games: { row: SearchRow; home: boolean }[] }>();
  for (const r of rows) {
    const sides = slateSides(r);
    for (const home of [false, true]) {
      const name = home ? r.home : r.away;
      if (!name || !nameMatches(name, q)) continue;
      const key = `${r.league}|${name}`;
      const t = byTeam.get(key) ?? { name, league: r.league, face: {}, games: [] };
      const face = sides?.[home ? 1 : 0];
      if (face && Object.keys(face).length) t.face = face;
      t.games.push({ row: r, home });
      byTeam.set(key, t);
    }
  }
  const nq = norm(q);
  const hits: { rank: number; hit: TeamHit }[] = [];
  for (const t of byTeam.values()) {
    const g = pick(t.games, now);
    const n = norm(t.name);
    const live = isLive(g.row);
    // A name that starts with the text beats one that only contains it; a live game first among equals.
    const rank = (n.startsWith(nq) ? 0 : 2) + (live ? 0 : 1);
    hits.push({
      rank,
      hit: {
        name: t.name, league: t.league, face: t.face, gameId: g.row.id,
        opponent: g.home ? g.row.away : g.row.home, home: g.home, at: g.row.starts_at, status: g.row.status,
      },
    });
  }
  hits.sort((a, b) => a.rank - b.rank || ORDER(a.hit.league) - ORDER(b.hit.league) || a.hit.name.localeCompare(b.hit.name));
  return hits.slice(0, SEARCH_MAX_TEAMS).map((h) => h.hit);
}

/** The PostgREST or-filter for the home and away names. */
export function searchFilter(pat: string): string {
  const letters = pat.replace(/\*/g, "").length;
  const cols = ["home", "away"];
  if (letters >= SEARCH_ANYWHERE) return cols.map((c) => `${c}.ilike.*${pat}*`).join(",");
  // The word-start form holds a space: quoted, as PostgREST takes a value with one.
  return cols.flatMap((c) => [`${c}.ilike.${pat}*`, `${c}.ilike."* ${pat}*"`]).join(",");
}
