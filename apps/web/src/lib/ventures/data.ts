import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { weekStart } from "@/lib/goals/period";
import { GOAL_COLUMNS, type Goal } from "@/lib/goals/types";

import {
  DATE_COLUMNS,
  VENTURE_COLUMNS,
  WORKSTREAM_COLUMNS,
  type LogEntry,
  type TodayRow,
  type Venture,
  type VentureDate,
  type Workstream,
} from "./types";

// Reads run with the owner's session, so db/022's RLS applies.

function fail(what: string, error: { message: string } | null): never {
  throw new Error(`Couldn't read ${what}: ${error?.message ?? "unknown error"}`);
}

export type VentureListItem = Venture & { open_dates: VentureDate[] };

/** Every venture with its open dates, in the owner's order. */
export async function listVentures(supabase: SupabaseClient): Promise<VentureListItem[]> {
  const [v, d] = await Promise.all([
    supabase.from("ventures").select(VENTURE_COLUMNS).order("sort_order").order("created_at"),
    supabase.from("venture_dates").select(DATE_COLUMNS).is("done_at", null),
  ]);
  if (v.error) fail("ventures", v.error);
  if (d.error) fail("venture dates", d.error);
  const dates = d.data as VentureDate[];
  return (v.data as Venture[]).map((x) => ({ ...x, open_dates: dates.filter((dt) => dt.venture_id === x.id) }));
}

/**
 * For goals: active ventures as chips, and every venture's name (archived
 * included) for the tag line of a goal already linked.
 */
export async function ventureLinks(
  supabase: SupabaseClient,
): Promise<{ options: { id: string; name: string }[]; names: Record<string, string> }> {
  const { data, error } = await supabase
    .from("ventures")
    .select("id, name, archived_at")
    .order("sort_order")
    .order("created_at");
  if (error) fail("ventures", error);
  return {
    options: data.filter((v) => !v.archived_at).map(({ id, name }) => ({ id, name })),
    names: Object.fromEntries(data.map((v) => [v.id, v.name])),
  };
}

export type VentureDetail = {
  venture: Venture;
  workstreams: Workstream[];
  dates: VentureDate[];
  log: LogEntry[];
  goals: Goal[];
};

/** One venture and everything under it; null if the slug is not the owner's. */
export async function ventureBySlug(
  supabase: SupabaseClient,
  slug: string,
  today: string,
): Promise<VentureDetail | null> {
  const v = await supabase.from("ventures").select(VENTURE_COLUMNS).eq("slug", slug).maybeSingle();
  if (v.error) fail("the venture", v.error);
  if (!v.data) return null;
  const venture = v.data as Venture;
  const [w, d, l, g] = await Promise.all([
    supabase.from("venture_workstreams").select(WORKSTREAM_COLUMNS).eq("venture_id", venture.id).order("sort_order").order("created_at"),
    supabase.from("venture_dates").select(DATE_COLUMNS).eq("venture_id", venture.id).order("due_on"),
    supabase.from("venture_log").select("id, kind, entry, created_at").eq("venture_id", venture.id).order("id", { ascending: false }),
    supabase
      .from("goals")
      .select(GOAL_COLUMNS)
      .eq("venture_id", venture.id)
      .eq("horizon", "weekly")
      .eq("period_start", weekStart(today)),
  ]);
  if (w.error) fail("workstreams", w.error);
  if (d.error) fail("dates", d.error);
  if (l.error) fail("the log", l.error);
  if (g.error) fail("goals", g.error);
  return {
    venture,
    workstreams: w.data as Workstream[],
    dates: d.data as VentureDate[],
    log: l.data as LogEntry[],
    goals: (g.data as Omit<Goal, "moved">[]).map((x) => ({ ...x, moved: false })),
  };
}

/** v_venture_today: open dates due today or earlier, oldest first. */
export async function ventureToday(supabase: SupabaseClient): Promise<TodayRow[]> {
  const { data, error } = await supabase
    .from("v_venture_today")
    .select("id, venture_id, venture_name, venture_slug, workstream_name, label, due_on, overdue")
    .order("due_on")
    .order("label");
  if (error) fail("today's venture dates", error);
  return data as TodayRow[];
}
