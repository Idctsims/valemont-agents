import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { PAGE_SIZE, otherOutlets, type FeedStory } from "./types";

// Reads only: the worker writes the feed (db/032), the owner reads it through
// RLS. The real selector is v_feed_stories (kept, non-test, linkable stories).
// A request carrying the verified e2e marker also sees test stories
// (src/lib/capital/marker.ts), read from stories itself.

type ViewRow = {
  id: number;
  title: string;
  summary: string | null;
  item_count: number;
  lead_item_id: number;
  lead_url: string;
  lead_published_at: string;
  source_name: string;
};

type TestRow = {
  id: number;
  title: string;
  summary: string | null;
  item_count: number;
  is_test: boolean;
  lead_item_id: number;
  lead: { url: string | null; published_at: string; source: { name: string } | null } | null;
};

async function outletsFor(
  supabase: SupabaseClient,
  rows: { id: number; leadItemId: number; sourceName: string; itemCount: number }[],
): Promise<Map<number, FeedStory["outlets"]>> {
  const multi = rows.filter((r) => r.itemCount > 1);
  const out = new Map<number, FeedStory["outlets"]>();
  if (multi.length === 0) return out;
  const { data, error } = await supabase
    .from("content_items")
    .select("id, story_id, url, source:sources(name)")
    .in("story_id", multi.map((r) => r.id))
    .not("url", "is", null)
    .order("id");
  if (error) throw new Error(`Couldn't read the other outlets: ${error.message}`);
  const byStory = new Map<number, { name: string; url: string }[]>();
  for (const it of (data ?? []) as unknown as { id: number; story_id: number; url: string; source: { name: string } | null }[]) {
    const list = byStory.get(it.story_id) ?? [];
    list.push({ name: it.source?.name ?? "Unknown", url: it.url });
    byStory.set(it.story_id, list);
  }
  for (const r of multi) out.set(r.id, otherOutlets(r.sourceName, byStory.get(r.id) ?? []));
  return out;
}

/** One page of a pillar's stories, newest first. `hasMore` when another page exists. */
export async function latestStories(
  supabase: SupabaseClient,
  opts: { pillar: "home" | "sports"; page: number; withTests?: boolean },
): Promise<{ stories: FeedStory[]; hasMore: boolean }> {
  const from = (opts.page - 1) * PAGE_SIZE;
  // One extra row tells whether a next page exists.
  const to = from + PAGE_SIZE;
  let base: Omit<FeedStory, "outlets">[];
  let meta: { id: number; leadItemId: number; sourceName: string; itemCount: number }[];

  if (opts.withTests) {
    const { data, error } = await supabase
      .from("stories")
      .select("id, title, summary, item_count, is_test, lead_item_id, lead:content_items!stories_lead_item_fk(url, published_at, source:sources(name))")
      .contains("pillars", [opts.pillar])
      .is("filtered_reason", null)
      .not("lead_item_id", "is", null)
      .order("first_seen_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, to);
    if (error) throw new Error(`Couldn't read the feed: ${error.message}`);
    const rows = ((data ?? []) as unknown as TestRow[]).filter((r) => r.lead?.url);
    base = rows.map((r) => ({
      id: r.id, title: r.title, summary: r.summary, sourceName: r.lead!.source?.name ?? "Unknown",
      url: r.lead!.url!, publishedAt: r.lead!.published_at, itemCount: r.item_count, isTest: r.is_test,
    }));
    meta = rows.map((r) => ({ id: r.id, leadItemId: r.lead_item_id, sourceName: r.lead!.source?.name ?? "Unknown", itemCount: r.item_count }));
  } else {
    const { data, error } = await supabase
      .from("v_feed_stories")
      .select("id, title, summary, item_count, lead_item_id, lead_url, lead_published_at, source_name")
      .contains("pillars", [opts.pillar])
      .order("lead_published_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, to);
    if (error) throw new Error(`Couldn't read the feed: ${error.message}`);
    const rows = (data ?? []) as ViewRow[];
    base = rows.map((r) => ({
      id: r.id, title: r.title, summary: r.summary, sourceName: r.source_name, url: r.lead_url,
      publishedAt: r.lead_published_at, itemCount: r.item_count, isTest: false,
    }));
    meta = rows.map((r) => ({ id: r.id, leadItemId: r.lead_item_id, sourceName: r.source_name, itemCount: r.item_count }));
  }

  const hasMore = base.length > PAGE_SIZE;
  const page = base.slice(0, PAGE_SIZE);
  const outlets = await outletsFor(supabase, meta.slice(0, PAGE_SIZE));
  return { stories: page.map((s) => ({ ...s, outlets: outlets.get(s.id) ?? [] })), hasMore };
}

// ------------------------------------------------------------ /settings/sources

export type SourceRow = {
  id: number;
  slug: string;
  name: string;
  pillars: string[];
  kind: "rss" | "gnews";
  active: boolean;
  priority: number;
  last_ok_at: string | null;
  last_fetched_at: string | null;
  last_status: "ok" | "not_modified" | "error" | null;
  last_error: string | null;
  consecutive_failures: number;
  notes: string | null;
  items24h: number;
  filtered24h: number;
  decodeFailed24h: number;
};

export type FilteredRow = {
  id: number;
  title: string;
  filtered_reason: string;
  first_seen_at: string;
  source_name: string;
  url: string | null;
};

export async function sourcesHealth(supabase: SupabaseClient, sinceIso: string) {
  const [sources, stats, filtered, rules] = await Promise.all([
    supabase
      .from("sources")
      .select("id, slug, name, pillars, kind, active, priority, last_ok_at, last_fetched_at, last_status, last_error, consecutive_failures, notes")
      .eq("is_test", false)
      .order("active", { ascending: false })
      .order("name"),
    supabase.from("v_feed_source_stats").select("source_id, items_24h, filtered_24h, decode_failed_24h"),
    supabase
      .from("v_feed_filtered")
      .select("id, title, filtered_reason, first_seen_at, source_name, url")
      .gte("first_seen_at", sinceIso)
      .order("first_seen_at", { ascending: false })
      .limit(300),
    supabase.from("feed_rules").select("id, pattern"),
  ]);
  for (const r of [sources, stats, filtered, rules]) {
    if (r.error) throw new Error(`Couldn't read the sources: ${r.error.message}`);
  }
  const byId = new Map(
    ((stats.data ?? []) as { source_id: number; items_24h: number; filtered_24h: number; decode_failed_24h: number }[]).map(
      (s) => [s.source_id, s],
    ),
  );
  const rows: SourceRow[] = ((sources.data ?? []) as Omit<SourceRow, "items24h" | "filtered24h" | "decodeFailed24h">[]).map((s) => ({
    ...s,
    items24h: Number(byId.get(s.id)?.items_24h ?? 0),
    filtered24h: Number(byId.get(s.id)?.filtered_24h ?? 0),
    decodeFailed24h: Number(byId.get(s.id)?.decode_failed_24h ?? 0),
  }));
  const patterns = new Map(((rules.data ?? []) as { id: number; pattern: string }[]).map((r) => [r.id, r.pattern]));
  return { rows, filtered: (filtered.data ?? []) as FilteredRow[], patterns };
}
