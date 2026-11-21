import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { LivePatch, SlateRow, Status } from "./slate";
import { slateSides, type TeamRow } from "./teams";
import type { TvTier } from "./tiers";

// Reads only, through the owner's session (RLS on sports_events, db/065),
// via db/068's narrow functions, never Realtime. One exception: the slate's
// team faces (logo, colour, city, nickname), read from sports_events by id,
// eight JSON paths and nothing else (owner, Nov 20: the cards with logos).
//   gameday_slate   the page: ~29 KB for the five on a weekday (measured
//                   Nov 5 on production's rows), ~50 KB with All sports
//   gameday_live    the 60 s poll: ~35 bytes per polled game
//   gameday_event   the detail page: one object, ~1 KB

export async function loadSlate(
  supabase: SupabaseClient,
  opts: { day: string; all: boolean; withTests: boolean },
): Promise<SlateRow[]> {
  const { data, error } = await supabase.rpc("gameday_slate", { p_day: opts.day, p_all: opts.all, p_tests: opts.withTests });
  if (error) throw new Error(`Couldn't read Game Day: ${error.message}`);
  const rows = (data ?? []) as SlateRow[];
  return withTeams(supabase, rows);
}

const SIDES = [
  "id",
  "aloc:participants->away->>loc", "an:participants->away->>nick",
  "al:participants->away->>logo", "ac:participants->away->>color",
  "hloc:participants->home->>loc", "hn:participants->home->>nick",
  "hl:participants->home->>logo", "hc:participants->home->>color",
].join(",");

/** The games' logos and colours. Cosmetic: a failed read leaves the cards without them, logged, never a broken page. */
async function withTeams(supabase: SupabaseClient, rows: SlateRow[]): Promise<SlateRow[]> {
  const ids = rows.filter((r) => r.home && r.away).map((r) => r.id);
  if (ids.length === 0) return rows;
  const { data, error } = await supabase.from("sports_events").select(SIDES).in("id", ids);
  if (error) {
    console.error(`gameday: team faces unread: ${error.message}`);
    return rows;
  }
  const byId = new Map<number, NonNullable<SlateRow["tm"]>>();
  for (const t of (data ?? []) as unknown as TeamRow[]) {
    const sides = slateSides(t);
    if (sides) byId.set(t.id, sides);
  }
  return rows.map((r) => {
    const tm = byId.get(r.id);
    return tm ? { ...r, tm } : r;
  });
}

export async function loadLive(supabase: SupabaseClient, ids: number[]): Promise<LivePatch[]> {
  const { data, error } = await supabase.rpc("gameday_live", { p_ids: ids });
  if (error) throw new Error(`Couldn't read the live scores: ${error.message}`);
  return (data ?? []) as LivePatch[];
}

export type KalshiState = "linked" | "not_listed" | "not_linked" | "out_of_scope" | null;

export type GameEvent = {
  id: number;
  league: string;
  league_label: string;
  kind: string;
  day: string;
  starts_at: string;
  time_tbd: boolean;
  title: string;
  event_name: string;
  session: string | null;
  home: string | null;
  away: string | null;
  participants:
    | { home?: { name?: string; rank?: number; abbr?: string }; away?: { name?: string; rank?: number; abbr?: string } }
    | { name?: string; winner?: boolean; sets?: (number | string)[] }[]
    | null;
  broadcast: string[];
  tv_tier: TvTier;
  venue: string | null;
  neutral_site: boolean | null;
  status: Status;
  status_detail: string | null;
  period: number | null;
  clock: string | null;
  score: Record<string, unknown> | null;
  is_test: boolean;
  kalshi: { state: KalshiState; reason: string | null; ticker: string | null; series: string | null };
  sessions: { id: number; session: string; starts_at: string; time_tbd: boolean; status: Status }[] | null;
  standings: { session: string; status: Status; score: Record<string, unknown> } | null;
};

export async function loadEvent(supabase: SupabaseClient, id: number): Promise<GameEvent | null> {
  const { data, error } = await supabase.rpc("gameday_event", { p_id: id });
  if (error) throw new Error(`Couldn't read that game: ${error.message}`);
  return (data ?? null) as GameEvent | null;
}
