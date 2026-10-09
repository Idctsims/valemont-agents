import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { addDays, monthStart, weekStart } from "./period";
import { GOAL_COLUMNS, type Goal } from "./types";

// Reads run with the owner's session, so db/021's RLS applies. The carry
// rules are SQL (carry_over_goals, goal_periods_to_roll): this file only
// calls them, it never re-implements them.

type Row = Omit<Goal, "moved" | "pending">;

const ROLLOVER_MAX_STEPS = 200;

/**
 * The rollover safety net. Carries every ended week and month not yet rolled,
 * oldest first, exactly as the worker's goals_rollover job does. Idempotent:
 * a period already carried inserts nothing. If Railway is down at midnight,
 * opening the app still rolls the week.
 *
 * Called by the Goals page and Home while they render, before they read, so
 * the first paint already shows carried goals.
 */
export async function ensureRollover(supabase: SupabaseClient): Promise<number> {
  const roll = async (horizon: "weekly" | "monthly") => {
    let carried = 0;
    for (let step = 0; step < ROLLOVER_MAX_STEPS; step++) {
      const due = await supabase.rpc("goal_periods_to_roll", { p_horizon: horizon });
      if (due.error) throw new Error(`Couldn't check goal rollover: ${due.error.message}`);
      const periods = (due.data ?? []) as string[];
      if (periods.length === 0) return carried;
      const res = await supabase.rpc("carry_over_goals", { p_horizon: horizon, p_from: periods[0] });
      if (res.error) throw new Error(`Couldn't carry ${horizon} goals: ${res.error.message}`);
      if (res.data === 0) throw new Error(`Goal rollover: ${horizon} ${periods[0]} is due but nothing carried.`);
      carried += res.data as number;
    }
    throw new Error(`Goal rollover: ${horizon} still has periods due after ${ROLLOVER_MAX_STEPS} steps.`);
  };
  const [weekly, monthly] = await Promise.all([roll("weekly"), roll("monthly")]);
  return weekly + monthly;
}

/** Mark rows whose id appears as another row's carried_from. */
function withMoved(rows: Row[], children: { carried_from: string | null }[]): Goal[] {
  const parents = new Set(children.map((c) => c.carried_from).filter(Boolean));
  return rows.map((r) => ({ ...r, moved: parents.has(r.id) }));
}

async function childrenOf(supabase: SupabaseClient, ids: string[]) {
  if (ids.length === 0) return [];
  const { data, error } = await supabase.from("goals").select("carried_from").in("carried_from", ids);
  if (error) throw new Error(`Couldn't read goals: ${error.message}`);
  return data;
}

async function period(
  supabase: SupabaseClient,
  horizon: "weekly" | "monthly",
  start: string,
): Promise<Goal[]> {
  const { data, error } = await supabase
    .from("goals")
    .select(GOAL_COLUMNS)
    .eq("horizon", horizon)
    .eq("period_start", start);
  if (error) throw new Error(`Couldn't read goals: ${error.message}`);
  const rows = data as Row[];
  return withMoved(rows, await childrenOf(supabase, rows.map((r) => r.id)));
}

export function weekGoals(supabase: SupabaseClient, today: string): Promise<Goal[]> {
  return period(supabase, "weekly", weekStart(today));
}

export function monthGoals(supabase: SupabaseClient, today: string): Promise<Goal[]> {
  return period(supabase, "monthly", monthStart(today));
}

export async function longTermGoals(supabase: SupabaseClient): Promise<Goal[]> {
  const { data, error } = await supabase.from("goals").select(GOAL_COLUMNS).eq("horizon", "long_term");
  if (error) throw new Error(`Couldn't read goals: ${error.message}`);
  return withMoved(data as Row[], []);
}

export type PastWeek = { monday: string; goals: Goal[] };

/** Every past week with goals, newest first, up to `weeks` back. */
export async function weekHistory(
  supabase: SupabaseClient,
  today: string,
  weeks = 26,
): Promise<PastWeek[]> {
  const current = weekStart(today);
  const { data, error } = await supabase
    .from("goals")
    .select(GOAL_COLUMNS)
    .eq("horizon", "weekly")
    .gte("period_start", addDays(current, -7 * weeks))
    .lte("period_start", addDays(current, 7));
  if (error) throw new Error(`Couldn't read goal history: ${error.message}`);
  const rows = data as Row[];
  // Children of a past week's goals sit in the following week, which this
  // range includes (up to next week, for goals moved on from this one).
  const goals = withMoved(rows, rows);
  const byWeek = new Map<string, Goal[]>();
  for (const g of goals) {
    if (!g.period_start || g.period_start >= current) continue;
    byWeek.set(g.period_start, [...(byWeek.get(g.period_start) ?? []), g]);
  }
  return [...byWeek.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([monday, list]) => ({ monday, goals: list }));
}
