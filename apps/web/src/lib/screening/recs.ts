import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { Kind } from "@/lib/tmdb/client";

// Recommendations, the seen-it pass, title reactions and "In the news"
// (Chat 4 Phase 4; db/053). Reads only, through the owner's session (RLS).
// The worker writes the scores; the exclusions are applied by the database
// at read time (screening_for_you), so a tap removes a card for good before
// the next re-rank. A test row shows only to a request with the verified e2e
// marker (`withTests`).

export const FOR_YOU = 12;

export type Rec = {
  pos: number;
  titleId: number;
  tmdbId: number;
  kind: Kind;
  name: string;
  releaseDate: string | null;
  posterPath: string | null;
  because: string | null;
  tonight: string[];
  isTest: boolean;
};

type RecRow = {
  pos: number; title_id: number; tmdb_id: number; kind: Kind; name: string; release_date: string | null;
  poster_path: string | null; because_name: string | null; tonight: string[] | null; is_test: boolean;
};

/** The For you row: db/053 screening_for_you (exclusions, floor, balance). */
export async function forYou(supabase: SupabaseClient, opts: { withTests?: boolean; limit?: number } = {}): Promise<Rec[]> {
  const { data, error } = await supabase.rpc("screening_for_you", { p_limit: opts.limit ?? FOR_YOU, p_with_tests: !!opts.withTests });
  if (error) throw new Error(`Couldn't read your recommendations: ${error.message}`);
  return ((data ?? []) as RecRow[]).map((r) => ({
    pos: r.pos, titleId: r.title_id, tmdbId: r.tmdb_id, kind: r.kind, name: r.name, releaseDate: r.release_date,
    posterPath: r.poster_path, because: r.because_name, tonight: r.tonight ?? [], isTest: r.is_test,
  }));
}

export type TitleReaction = "love" | "neutral" | "not_interested";
export type SeenAnswer = "seen_loved" | "seen_not_for_me" | "not_seen";

export type PassTitle = {
  titleId: number;
  tmdbId: number;
  kind: Kind;
  name: string;
  releaseDate: string | null;
  posterPath: string | null;
  overview: string | null;
  answer: SeenAnswer | null;
  isTest: boolean;
};

type PassRowDb = { title_id: number; pos: number; is_test: boolean; title: {
  tmdb_id: number; kind: Kind; name: string; release_date: string | null; poster_path: string | null; overview: string | null;
} | null };

/** The seen-it pass in order (a test title first, with the marker), each
 * with its latest answer (null: not answered yet). */
export async function seenPass(supabase: SupabaseClient, opts: { withTests?: boolean } = {}): Promise<PassTitle[]> {
  let q = supabase.from("screening_seen_pass")
    .select("title_id, pos, is_test, title:titles(tmdb_id, kind, name, release_date, poster_path, overview)");
  if (!opts.withTests) q = q.eq("is_test", false);
  // Test titles first (with the marker), the newest of them first.
  const { data, error } = await q.order("is_test", { ascending: false }).order("pos").order("title_id", { ascending: false });
  if (error) throw new Error(`Couldn't read the seen-it pass: ${error.message}`);
  const rows = ((data ?? []) as unknown as PassRowDb[]).filter((r) => r.title);
  const answers = await passAnswers(supabase, rows.map((r) => r.title_id));
  return rows.map((r) => ({
    titleId: r.title_id, tmdbId: r.title!.tmdb_id, kind: r.title!.kind, name: r.title!.name,
    releaseDate: r.title!.release_date, posterPath: r.title!.poster_path, overview: r.title!.overview,
    answer: answers.get(r.title_id) ?? null, isTest: r.is_test,
  }));
}

async function passAnswers(supabase: SupabaseClient, ids: number[]): Promise<Map<number, SeenAnswer>> {
  const out = new Map<number, SeenAnswer>();
  if (!ids.length) return out;
  const { data, error } = await supabase.from("v_title_state").select("title_id, seen_pass").in("title_id", ids);
  if (error) throw new Error(`Couldn't read your answers: ${error.message}`);
  for (const r of (data ?? []) as { title_id: number; seen_pass: SeenAnswer | null }[]) {
    if (r.seen_pass) out.set(r.title_id, r.seen_pass);
  }
  return out;
}

/** "N of M answered" for /screening and /settings/feed. */
export async function seenPassProgress(supabase: SupabaseClient, opts: { withTests?: boolean } = {}) {
  const pass = await seenPass(supabase, opts);
  return { total: pass.length, answered: pass.filter((p) => p.answer).length };
}

/** A title's latest reaction (Love / Neutral / Not interested), if any. */
export async function titleReaction(supabase: SupabaseClient, titleId: number): Promise<TitleReaction | null> {
  const { data, error } = await supabase.from("v_title_state").select("reaction").eq("title_id", titleId).maybeSingle();
  if (error) throw new Error(`Couldn't read your reaction: ${error.message}`);
  return ((data as { reaction: TitleReaction | null } | null)?.reaction) ?? null;
}

export type NewsStory = { storyId: number; title: string; summary: string | null; url: string; sourceName: string; firstSeenAt: string };

/** "In the news": up to 3 recent Home stories mentioning a title on Want or
 * Watching (db/053 title_news, through its title entity). */
export async function titleNews(supabase: SupabaseClient, titleId: number, opts: { withTests?: boolean } = {}): Promise<NewsStory[]> {
  const { data, error } = await supabase.rpc("title_news", { p_title_id: titleId, p_limit: 3, p_with_tests: !!opts.withTests });
  if (error) throw new Error(`Couldn't read the news: ${error.message}`);
  return ((data ?? []) as { story_id: number; title: string; summary: string | null; url: string; source_name: string; first_seen_at: string }[])
    .map((r) => ({ storyId: r.story_id, title: r.title, summary: r.summary, url: r.url, sourceName: r.source_name, firstSeenAt: r.first_seen_at }));
}

/** Trailers For you: story ids in the screening profile's order (db/053
 * screening_trailers_for_you), one page plus one row to tell a next page. */
export async function trailersForYouIds(supabase: SupabaseClient, page: number, pageSize: number, opts: { withTests?: boolean } = {}) {
  const { data, error } = await supabase.rpc("screening_trailers_for_you", {
    p_limit: pageSize + 1, p_offset: (page - 1) * pageSize, p_with_tests: !!opts.withTests,
  });
  if (error) throw new Error(`Couldn't rank the trailers: ${error.message}`);
  return ((data ?? []) as { story_id: number }[]).map((r) => r.story_id);
}
