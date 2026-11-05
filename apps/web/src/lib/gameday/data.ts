import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { LivePatch, SlateRow, Status } from "./slate";
import type { TvTier } from "./tiers";

// Reads only, through the owner's session (RLS on sports_events, db/065),
// via db/068's narrow functions: never the table itself, never Realtime.
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
  return (data ?? []) as SlateRow[];
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
