import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { loadSummary } from "@/lib/gameday/espn";
import type { GameSummary } from "@/lib/gameday/summary";
import { GAME_TICKER, loadKalshiLive } from "./kalshi-live";
import { normName, type GameLive, type GameSide, type PlayerLive } from "./parlays";

// The Parlays page's live count and player photos (Phase 4.5; docs/parlays_page.md §4).
// - A game under way or over: ESPN's summary, the path Game Day already uses
//   (lib/gameday/espn.ts: 15 s in Next's data cache while on, never stored),
//   cut down to the players the tickets name.
// - A game not started: ESPN's team rosters (cached a day), for the photos only.
// Test games ("e2e-" ids) never fetch.

const BASE = "https://site.api.espn.com/apis/site/v2/sports";
const TIMEOUT_MS = 6_000;
export const LIVE_GAMES_MAX = 40;
const NAMES_MAX = 40;

/** A game Game Day links (g), or one it does not, by its Kalshi game ticker (k, db/095). */
export type LiveRequest = { g?: number; k?: string; names: string[] };

type EventRow = { id: number; espn_id: string; league: string; kind: string; status: string; participants: unknown };

function teamId(p: unknown, side: GameSide): string | null {
  const t = p && typeof p === "object" ? (p as Record<string, { id?: unknown }>)[side] : undefined;
  return typeof t?.id === "string" && /^\d+$/.test(t.id) ? t.id : null;
}

/** ESPN's roster for one team: [full name, photo]. Flat (basketball, hockey) or grouped by position (football). */
async function roster(path: string, team: string): Promise<[string, string][]> {
  try {
    const res = await fetch(`${BASE}/${path}/teams/${team}/roster`, {
      next: { revalidate: 86_400 }, signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = (await res.json()) as { athletes?: unknown[] };
    const flat: unknown[] = [];
    for (const a of body.athletes ?? []) {
      const items = (a as { items?: unknown[] }).items;
      if (Array.isArray(items)) flat.push(...items);
      else flat.push(a);
    }
    const out: [string, string][] = [];
    for (const a of flat) {
      const name = (a as { displayName?: unknown }).displayName;
      const img = (a as { headshot?: { href?: unknown } }).headshot?.href;
      if (typeof name === "string" && typeof img === "string" && img.startsWith("https://a.espncdn.com/")) out.push([name, img]);
    }
    return out;
  } catch (e) {
    console.error(`parlays: ESPN roster failed (${path} ${team}): ${(e as Error).message}`);
    return [];
  }
}

function toInt(s: string | undefined): number | undefined {
  const n = s === undefined ? NaN : Number(s);
  return Number.isFinite(n) ? n : undefined;
}

/** A game's summary cut to the named players' box scores (keys "group:LABEL") and both sides' scores by period. */
export function liveFromSummary(s: GameSummary, names: Set<string>): GameLive {
  const players: Record<string, PlayerLive> = {};
  for (const side of ["away", "home"] as const) {
    for (const group of s.players[side]) {
      for (const r of group.rows) {
        const key = normName(r.full ?? r.name);
        if (!names.has(key)) continue;
        const p = (players[key] ??= { side, stats: {}, ...(r.img ? { img: r.img } : {}) });
        group.labels.forEach((label, i) => {
          if (r.stats[i] !== undefined && r.stats[i] !== "") p.stats![`${(group.key ?? "").toLowerCase()}:${label}`] = r.stats[i];
        });
      }
    }
  }
  const sideOf = (x: GameSummary["away"]) => ({ score: toInt(x.score), lines: x.lines.map((l) => toInt(l) ?? 0) });
  return { state: s.state ?? "in", detail: s.detail, away: sideOf(s.away), home: sideOf(s.home), players };
}

/** Before a game: the named players' photos from both teams' rosters. */
export async function rosterFaces(path: string, teams: Partial<Record<GameSide, string>>, names: Set<string>):
    Promise<Record<string, PlayerLive>> {
  const players: Record<string, PlayerLive> = {};
  if (!/^[a-z]+\/[a-z0-9.-]+$/.test(path) || names.size === 0) return players;
  await Promise.all((["home", "away"] as const).map(async (side) => {
    const id = teams[side];
    if (!id) return;
    for (const [name, img] of await roster(path, id)) {
      const key = normName(name);
      if (names.has(key)) players[key] = { img, side };
    }
  }));
  return players;
}

/** Each requested game's live state and the named players' box scores (or, before it starts, their photos). */
export async function loadParlayLive(supabase: SupabaseClient, req: LiveRequest[]): Promise<Record<string, GameLive>> {
  const wanted = new Map<number, Set<string>>();
  const kalshi = new Map<string, Set<string>>();
  for (const r of req.slice(0, LIVE_GAMES_MAX)) {
    const names = new Set<string>();
    for (const n of (Array.isArray(r.names) ? r.names : []).slice(0, NAMES_MAX)) if (typeof n === "string") names.add(normName(n));
    if (typeof r.k === "string" && GAME_TICKER.test(r.k)) {
      kalshi.set(r.k, new Set([...(kalshi.get(r.k) ?? []), ...names]));
      continue;
    }
    if (typeof r.g !== "number" || !Number.isSafeInteger(r.g) || r.g <= 0) continue;
    wanted.set(r.g, new Set([...(wanted.get(r.g) ?? []), ...names]));
  }
  // Games Game Day does not link: Kalshi's own live data (soccer), keyed "k:<ticker>".
  const fromKalshi = kalshi.size ? loadKalshiLive([...kalshi].map(([k, names]) => ({ k, names }))) : Promise.resolve({});
  if (wanted.size === 0) return fromKalshi;
  const { data, error } = await supabase
    .from("sports_events")
    .select("id, espn_id, league, kind, status, participants")
    .in("id", [...wanted.keys()]);
  if (error) throw new Error(`couldn't read the games: ${error.message}`);
  const rows = (data ?? []) as unknown as EventRow[];
  const leagues = [...new Set(rows.map((r) => r.league))];
  const { data: lg } = await supabase.from("sports_leagues").select("code, espn_path").in("code", leagues);
  const paths = new Map(((lg ?? []) as { code: string; espn_path: string | null }[]).map((x) => [x.code, x.espn_path]));

  const out: Record<string, GameLive> = {};
  await Promise.all(rows.map(async (row) => {
    const names = wanted.get(row.id) ?? new Set<string>();
    if (row.kind !== "game" || row.espn_id.startsWith("e2e-")) return;
    const path = paths.get(row.league);
    if (row.status === "scheduled") {
      // Photos only, from the two rosters.
      const teams = { home: teamId(row.participants, "home") ?? undefined, away: teamId(row.participants, "away") ?? undefined };
      out[row.id] = { state: "pre", players: path ? await rosterFaces(path, teams, names) : {} };
      return;
    }
    const res = await loadSummary(supabase, row.id);
    if (res.summary) out[row.id] = liveFromSummary(res.summary, names);
  }));
  return { ...out, ...(await fromKalshi) };
}
