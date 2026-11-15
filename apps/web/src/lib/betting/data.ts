import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { BoardCard, BoardEvent, BoardGame, Credits, Kind, SportChip } from "./board";
import type { ComboState, Quote } from "./combo";
import type { GameMeta, GameTabs, Tab, TabEvent } from "./game";

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

/** The new board's cards (db/082): kalshi_board_games' counts plus each game's moneyline prices and
 *  whether it holds a claimed edge. One sport, every game; the Claimed edges filter is applied here. */
export async function loadCards(supabase: SupabaseClient, sport: string, withTests: boolean): Promise<BoardCard[]> {
  const { data, error } = await supabase.rpc("kalshi_board_cards", { p_sport: sport, p_tests: withTests });
  if (error) throw new Error(`Couldn't read the games: ${error.message}`);
  return (data ?? []) as BoardCard[];
}

export async function loadGame(
  supabase: SupabaseClient,
  opts: { game: string; kind: Kind | null; combo: boolean; withTests: boolean; tickers?: boolean },
): Promise<BoardEvent[]> {
  // p_tickers (db/077) only in Combo mode: off, the call is db/074's.
  const { data, error } = await supabase.rpc("kalshi_board_game", {
    p_game: opts.game, p_kind: opts.kind, p_combo_only: opts.combo, p_tests: opts.withTests,
    ...(opts.tickers ? { p_tickers: true } : {}),
  });
  if (error) throw new Error(`Couldn't read that game's markets: ${error.message}`);
  return (data ?? []) as BoardEvent[];
}

// ---- The game page (db/079): the three tabs' counts, and one tab (one stat
// of the Players tab) at a time. The header reads the game's own event row
// (select-only for the owner, db/071), the one read here not through a function.

/** The game's header: its title, start, sport and league, from its own event (else any of its events). */
export async function loadGameMeta(supabase: SupabaseClient, game: string, withTests: boolean): Promise<GameMeta | null> {
  let q = supabase.from("kalshi_board_events")
    .select("event_ticker, series_ticker, game_title, starts_at, milestone_start, is_test")
    .eq("game_ticker", game)
    .limit(50);
  if (!withTests) q = q.eq("is_test", false);
  const { data, error } = await q;
  if (error) throw new Error(`Couldn't read the game: ${error.message}`);
  const rows = (data ?? []) as { event_ticker: string; series_ticker: string; game_title: string | null;
                                 starts_at: string | null; milestone_start: string | null; is_test: boolean }[];
  if (rows.length === 0) return null;
  const ev = rows.find((r) => r.event_ticker === game) ?? rows[0];
  const { data: s, error: e2 } = await supabase.from("kalshi_series_board")
    .select("sport, league").eq("series_ticker", ev.series_ticker).maybeSingle();
  if (e2) throw new Error(`Couldn't read the game's series: ${e2.message}`);
  const series = (s ?? null) as { sport: string | null; league: string | null } | null;
  return { g: game, title: ev.game_title ?? game, startsAt: ev.starts_at ?? ev.milestone_start,
           sport: series?.sport ?? null, league: series?.league ?? null, test: ev.is_test };
}

export async function loadGameTabs(supabase: SupabaseClient, game: string, withTests: boolean): Promise<GameTabs> {
  const { data, error } = await supabase.rpc("kalshi_board_game_tabs", { p_game: game, p_tests: withTests });
  if (error) throw new Error(`Couldn't read the game's tabs: ${error.message}`);
  const d = (data ?? {}) as Partial<GameTabs>;
  return { lines: Number(d.lines ?? 0), players: Number(d.players ?? 0), periods: Number(d.periods ?? 0),
           other: Number(d.other ?? 0), stats: Array.isArray(d.stats) ? d.stats : [], asof: d.asof ?? null };
}

/** One tab's events; on the Players tab one stat (OTHER_STAT, "", for markets with none). */
export async function loadGameTab(
  supabase: SupabaseClient, game: string, tab: Tab, stat: string | null, withTests: boolean,
): Promise<TabEvent[]> {
  const { data, error } = await supabase.rpc("kalshi_board_game_tab", {
    p_game: game, p_tab: tab, p_tests: withTests, p_stat: tab === "players" ? stat : null,
  });
  if (error) throw new Error(`Couldn't read that tab: ${error.message}`);
  return (data ?? []) as TabEvent[];
}

/** The Odds API's month (db/073 odds_api_usage): spent / 500 on /betting. */
export async function loadCredits(supabase: SupabaseClient): Promise<Credits> {
  const { data, error } = await supabase.rpc("odds_api_usage");
  if (error) throw new Error(`Couldn't read the Odds API credits: ${error.message}`);
  const d = (data ?? {}) as Partial<Credits>;
  return { used: d.used === null || d.used === undefined ? null : Number(d.used),
           spent: Number(d.spent ?? 0), allowance: Number(d.allowance ?? 500),
           remaining: d.remaining === null || d.remaining === undefined ? null : Number(d.remaining),
           calls: Number(d.calls ?? 0) };
}

// ---- Combo pricing (db/076): the owner inserts a request (its trigger queues
// the worker's price_combo job) and reads it back with its quote.

/** The new request's id. A refusal (db/077's rate limit, a malformed leg) is the guard's message. */
export async function createComboRequest(
  supabase: SupabaseClient, legs: { market_ticker: string; side: "yes" | "no" }[],
): Promise<{ id: number } | { refused: string }> {
  const { data, error } = await supabase.from("combo_requests").insert({ legs }).select("id").single();
  if (error) {
    if (/combo_requests:/.test(error.message)) return { refused: error.message };
    throw new Error(`Couldn't ask for a price: ${error.message}`);
  }
  return { id: Number((data as { id: number }).id) };
}

export async function loadComboRequest(supabase: SupabaseClient, id: number): Promise<ComboState | null> {
  const { data, error } = await supabase
    .from("combo_requests")
    .select("status, reason, created_at, answered_at, combo_quotes(naive_p, fair_p, se, blocks, kalshi_naive_cost, "
      + "combo_status, combo_reason, prior_pairs, unmodeled_pairs, rho_version, compute_ms)")
    .eq("id", id)
    .maybeSingle();
  if (error) throw new Error(`Couldn't read the price: ${error.message}`);
  if (!data) return null;
  const r = data as unknown as {
    status: ComboState["status"]; reason: string | null; created_at: string; answered_at: string | null;
    combo_quotes: Quote | Quote[] | null;
  };
  const q = Array.isArray(r.combo_quotes) ? r.combo_quotes[0] : r.combo_quotes;
  if (r.status === "answered" && q) {
    return { status: "answered", createdAt: r.created_at, answeredAt: r.answered_at ?? r.created_at,
             quote: { ...q, naive_p: Number(q.naive_p), fair_p: Number(q.fair_p),
                      se: q.se === null ? null : Number(q.se),
                      kalshi_naive_cost: q.kalshi_naive_cost === null ? null : Number(q.kalshi_naive_cost) } };
  }
  if (r.status === "refused" || r.status === "failed") {
    return { status: r.status, reason: r.reason ?? "", createdAt: r.created_at };
  }
  return { status: "queued", createdAt: r.created_at };
}
