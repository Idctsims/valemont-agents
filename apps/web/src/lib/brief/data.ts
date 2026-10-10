import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { latestPerDate, type Brief, type BriefBody } from "./types";

// Reads only: the worker writes briefs (db/030), the owner reads them through
// RLS. The real view is v_briefs_latest (latest row per date, test briefs
// never shown). A request carrying the verified e2e marker also sees test
// briefs (src/lib/capital/marker.ts), read from briefs itself.

type Row = {
  id: number;
  brief_date: string;
  created_at: string;
  body: BriefBody;
  fallback: boolean;
  model: string | null;
  word_count: number;
  is_test?: boolean;
};

const toBrief = (r: Row): Brief => ({
  id: r.id,
  date: r.brief_date,
  createdAt: r.created_at,
  body: r.body,
  fallback: r.fallback,
  model: r.model,
  wordCount: r.word_count,
  isTest: !!r.is_test,
});

const COLUMNS = "id, brief_date, created_at, body, fallback, model, word_count";

/** Every brief's latest row, newest date first, at most `limit` dates. */
export async function briefHistory(
  supabase: SupabaseClient,
  opts: { withTests?: boolean; limit?: number } = {},
): Promise<Brief[]> {
  const limit = opts.limit ?? 60;
  if (opts.withTests) {
    const { data, error } = await supabase
      .from("briefs")
      .select(`${COLUMNS}, is_test`)
      .eq("kind", "morning")
      .order("id", { ascending: false })
      .limit(limit * 3);
    if (error) throw new Error(`Couldn't read briefs: ${error.message}`);
    return latestPerDate((data as Row[]).map(toBrief)).slice(0, limit);
  }
  const { data, error } = await supabase
    .from("v_briefs_latest")
    .select(COLUMNS)
    .eq("kind", "morning")
    .order("brief_date", { ascending: false })
    .limit(limit);
  if (error) throw new Error(`Couldn't read briefs: ${error.message}`);
  return (data as Row[]).map(toBrief);
}

/** The latest brief for one date, or null. */
export async function briefFor(supabase: SupabaseClient, day: string, opts: { withTests?: boolean } = {}): Promise<Brief | null> {
  const query = opts.withTests
    ? supabase.from("briefs").select(`${COLUMNS}, is_test`).eq("kind", "morning").eq("brief_date", day).order("id", { ascending: false }).limit(1)
    : supabase.from("v_briefs_latest").select(COLUMNS).eq("kind", "morning").eq("brief_date", day).limit(1);
  const { data, error } = await query;
  if (error) throw new Error(`Couldn't read the brief: ${error.message}`);
  return data?.length ? toBrief(data[0] as Row) : null;
}
