import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { BoardEvent, BoardGame, Credits, Kind, SportChip } from "./board";

// Reads only, through the owner's session (RLS on db/071's tables), via its
// narrow functions: never the tables themselves. One sport at a time with its
// games collapsed (the page); one game's markets on expand (the action).

export async function loadSports(supabase: SupabaseClient, withTests: boolean): Promise<SportChip[]> {
  const { data, error } = await supabase.rpc("kalshi_board_sports", { p_tests: withTests });
  if (error) throw new Error(`Couldn't read the board: ${error.message}`);
  return (data ?? []) as SportChip[];
}

export async function loadGames(
  supabase: SupabaseClient,
  opts: { sport: string; kind: Kind | null; combo: boolean; withTests: boolean },
): Promise<BoardGame[]> {
  const { data, error } = await supabase.rpc("kalshi_board_games", {
    p_sport: opts.sport, p_kind: opts.kind, p_combo_only: opts.combo, p_tests: opts.withTests,
  });
  if (error) throw new Error(`Couldn't read the games: ${error.message}`);
  return (data ?? []) as BoardGame[];
}

export async function loadGame(
  supabase: SupabaseClient,
  opts: { game: string; kind: Kind | null; combo: boolean; withTests: boolean },
): Promise<BoardEvent[]> {
  const { data, error } = await supabase.rpc("kalshi_board_game", {
    p_game: opts.game, p_kind: opts.kind, p_combo_only: opts.combo, p_tests: opts.withTests,
  });
  if (error) throw new Error(`Couldn't read that game's markets: ${error.message}`);
  return (data ?? []) as BoardEvent[];
}

/** The Odds API's month (db/073 odds_api_usage): spent / 500 on /betting. */
export async function loadCredits(supabase: SupabaseClient): Promise<Credits> {
  const { data, error } = await supabase.rpc("odds_api_usage");
  if (error) throw new Error(`Couldn't read the Odds API credits: ${error.message}`);
  const d = (data ?? {}) as Partial<Credits>;
  return { spent: Number(d.spent ?? 0), allowance: Number(d.allowance ?? 500),
           remaining: d.remaining === null || d.remaining === undefined ? null : Number(d.remaining),
           calls: Number(d.calls ?? 0) };
}
