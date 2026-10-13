import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { PAGE_SIZE, hides, otherOutlets, type FeedStory, type Pillar } from "./types";

// Reads only: the worker writes the feed (db/032) and its scores (db/039);
// the owner reads through RLS, and writes only reactions (feed/actions.ts,
// /api/feed/open). The real selector is v_feed_stories (kept, non-test,
// linkable stories); For you is db/039's feed_for_you(), which reads it. A
// request carrying the verified e2e marker also sees test stories in Latest
// (src/lib/capital/marker.ts), read from stories itself.

type ViewRow = {
  id: number;
  title: string;
  summary: string | null;
  why_it_matters: string | null;
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
  why_it_matters: string | null;
  item_count: number;
  is_test: boolean;
  lead_item_id: number;
  lead: { url: string | null; published_at: string; source: { name: string } | null } | null;
};

type ForYouRow = ViewRow & { pos: number; reaction: string | null; floored: boolean | null; cross_pillar: boolean };

/** Hidden in Latest: the most recent stories whose latest reaction in this
 * pillar is neutral or not interested. For you hides them in SQL. */
const HIDDEN_LOOKBACK = 400;

async function hiddenIds(supabase: SupabaseClient, pillar: Pillar): Promise<number[]> {
  const { data, error } = await supabase
    .from("v_feed_reaction_latest")
    .select("story_id, reaction")
    .eq("pillar", pillar)
    .in("reaction", ["neutral", "not_interested"])
    .order("reacted_at", { ascending: false })
    .limit(HIDDEN_LOOKBACK);
  if (error) throw new Error(`Couldn't read your reactions: ${error.message}`);
  return ((data ?? []) as { story_id: number; reaction: string }[]).filter((r) => hides(r.reaction)).map((r) => r.story_id);
}

async function lovedIds(supabase: SupabaseClient, pillar: Pillar, ids: number[]): Promise<Set<number>> {
  if (ids.length === 0) return new Set();
  const { data, error } = await supabase
    .from("v_feed_reaction_latest")
    .select("story_id, reaction")
    .eq("pillar", pillar)
    .in("story_id", ids);
  if (error) throw new Error(`Couldn't read your reactions: ${error.message}`);
  return new Set(((data ?? []) as { story_id: number; reaction: string | null }[]).filter((r) => r.reaction === "love").map((r) => r.story_id));
}

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

type Base = Omit<FeedStory, "outlets" | "loved">;
type Meta = { id: number; leadItemId: number; sourceName: string; itemCount: number };

async function finish(
  supabase: SupabaseClient,
  pillar: Pillar,
  base: Base[],
  meta: Meta[],
  loved?: Set<number>,
): Promise<{ stories: FeedStory[]; hasMore: boolean }> {
  const hasMore = base.length > PAGE_SIZE;
  const page = base.slice(0, PAGE_SIZE);
  const [outlets, love] = await Promise.all([
    outletsFor(supabase, meta.slice(0, PAGE_SIZE)),
    loved ? Promise.resolve(loved) : lovedIds(supabase, pillar, page.map((s) => s.id)),
  ]);
  return { stories: page.map((s) => ({ ...s, outlets: outlets.get(s.id) ?? [], loved: love.has(s.id) })), hasMore };
}

/** One page of a pillar's stories, newest first, minus the ones the owner
 * dismissed (neutral, not interested). `hasMore` when another page exists. */
export async function latestStories(
  supabase: SupabaseClient,
  opts: { pillar: Pillar; page: number; withTests?: boolean },
): Promise<{ stories: FeedStory[]; hasMore: boolean }> {
  const from = (opts.page - 1) * PAGE_SIZE;
  // One extra row tells whether a next page exists.
  const to = from + PAGE_SIZE;
  const hidden = await hiddenIds(supabase, opts.pillar);
  // Story ids start at 1, so "(0)" excludes nothing when nothing is hidden.
  const notHidden = hidden.length ? `(${hidden.join(",")})` : "(0)";
  let base: Base[];
  let meta: Meta[];

  if (opts.withTests) {
    const { data, error } = await supabase
      .from("stories")
      .select("id, title, summary, why_it_matters, item_count, is_test, lead_item_id, lead:content_items!stories_lead_item_fk(url, published_at, source:sources(name))")
      .contains("pillars", [opts.pillar])
      .is("filtered_reason", null)
      .not("lead_item_id", "is", null)
      .not("id", "in", notHidden)
      .order("first_seen_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, to);
    if (error) throw new Error(`Couldn't read the feed: ${error.message}`);
    const rows = ((data ?? []) as unknown as TestRow[]).filter((r) => r.lead?.url);
    base = rows.map((r) => ({
      id: r.id, title: r.title, summary: r.summary, sourceName: r.lead!.source?.name ?? "Unknown",
      url: r.lead!.url!, publishedAt: r.lead!.published_at, itemCount: r.item_count, isTest: r.is_test,
      whyItMatters: r.why_it_matters,
    }));
    meta = rows.map((r) => ({ id: r.id, leadItemId: r.lead_item_id, sourceName: r.lead!.source?.name ?? "Unknown", itemCount: r.item_count }));
  } else {
    const { data, error } = await supabase
      .from("v_feed_stories")
      .select("id, title, summary, why_it_matters, item_count, lead_item_id, lead_url, lead_published_at, source_name")
      .contains("pillars", [opts.pillar])
      .not("id", "in", notHidden)
      .order("lead_published_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, to);
    if (error) throw new Error(`Couldn't read the feed: ${error.message}`);
    const rows = (data ?? []) as ViewRow[];
    base = rows.map(viewStory);
    meta = rows.map(viewMeta);
  }
  return finish(supabase, opts.pillar, base, meta);
}

function viewStory(r: ViewRow): Base {
  return {
    id: r.id, title: r.title, summary: r.summary, sourceName: r.source_name, url: r.lead_url,
    publishedAt: r.lead_published_at, itemCount: r.item_count, isTest: false, whyItMatters: r.why_it_matters,
  };
}

function viewMeta(r: ViewRow): Meta {
  return { id: r.id, leadItemId: r.lead_item_id, sourceName: r.source_name, itemCount: r.item_count };
}

/** One page of For you: db/039 feed_for_you() (the learned score minus
 * recency, the capped Alabama floor, at most 5 tier-1 sports stories on page
 * one; dismissed stories already left out). Never shows a test story. */
export async function forYouStories(
  supabase: SupabaseClient,
  opts: { pillar: Pillar; page: number },
): Promise<{ stories: FeedStory[]; hasMore: boolean }> {
  const { data, error } = await supabase.rpc("feed_for_you", {
    p_pillar: opts.pillar,
    p_limit: PAGE_SIZE + 1,
    p_offset: (opts.page - 1) * PAGE_SIZE,
  });
  if (error) throw new Error(`Couldn't read For you: ${error.message}`);
  const rows = (data ?? []) as ForYouRow[];
  const loved = new Set(rows.filter((r) => r.reaction === "love").map((r) => r.id));
  return finish(supabase, opts.pillar, rows.map(viewStory), rows.map(viewMeta), loved);
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

// --------------------------------------------------------------- /settings/feed

export type ProfileSummary = {
  id: number;
  pillar: Pillar;
  builtAt: string;
  loveCount: number;
  niCount: number;
  openCount: number;
  neutralCount: number;
  seedWeightLove: number;
  seedWeightNi: number;
  meanN: number;
  topics: Record<string, number>;
  sources: Record<string, number>;
  entities: Record<string, number>;
};

export type FollowSummary = { name: string; kind: string; tier: 1 | 2 | 3; pillar: Pillar | null; floor: boolean; sport: string | null };

type ProfileRowDb = {
  id: number; pillar: Pillar; built_at: string; love_count: number; ni_count: number; open_count: number;
  neutral_count: number; seed_weight_love: number; seed_weight_ni: number; mean_n: number;
  topic_weights: Record<string, number>; source_weights: Record<string, number>; entity_weights: Record<string, number>;
};

const PROFILE_HISTORY = 14;

function profileOf(r: ProfileRowDb): ProfileSummary {
  return {
    id: r.id, pillar: r.pillar, builtAt: r.built_at, loveCount: r.love_count, niCount: r.ni_count,
    openCount: r.open_count, neutralCount: r.neutral_count, seedWeightLove: Number(r.seed_weight_love),
    seedWeightNi: Number(r.seed_weight_ni), meanN: r.mean_n, topics: r.topic_weights ?? {},
    sources: r.source_weights ?? {}, entities: r.entity_weights ?? {},
  };
}

/** The learning engine in plain view: each pillar's profile history (newest
 * first, no vectors), the follows, and the reactions since the last rebuild. */
export async function feedProfile(supabase: SupabaseClient) {
  const [profiles, follows] = await Promise.all([
    supabase
      .from("interest_profiles")
      .select("id, pillar, built_at, love_count, ni_count, open_count, neutral_count, seed_weight_love, seed_weight_ni, mean_n, topic_weights, source_weights, entity_weights")
      .order("built_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(PROFILE_HISTORY * 2),
    supabase.from("follows").select("tier, pillar, floor, entity:entities(name, kind, sport)").order("tier"),
  ]);
  for (const r of [profiles, follows]) {
    if (r.error) throw new Error(`Couldn't read the feed profile: ${r.error.message}`);
  }
  const history = ((profiles.data ?? []) as ProfileRowDb[]).map(profileOf);
  const latest: Partial<Record<Pillar, ProfileSummary>> = {};
  for (const p of history) latest[p.pillar] ??= p;
  const since = Object.values(latest).map((p) => p!.builtAt).sort()[0];
  let pending = 0;
  if (since) {
    // Reactions on real stories only: the rebuild never reads one on a test
    // story (ledger FEED_SIGNALS_SQL), so the count must not either.
    const { count, error } = await supabase
      .from("feed_reactions")
      .select("id, stories!inner(is_test)", { count: "exact", head: true })
      .eq("stories.is_test", false)
      .gt("created_at", since);
    if (error) throw new Error(`Couldn't count new reactions: ${error.message}`);
    pending = count ?? 0;
  }
  const followRows = ((follows.data ?? []) as unknown as { tier: 1 | 2 | 3; pillar: Pillar | null; floor: boolean; entity: { name: string; kind: string; sport: string | null } | null }[])
    .filter((f) => f.entity)
    .map((f) => ({ name: f.entity!.name, kind: f.entity!.kind, sport: f.entity!.sport, tier: f.tier, pillar: f.pillar, floor: f.floor }))
    .sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name));
  return { latest, history, follows: followRows as FollowSummary[], pendingReactions: pending };
}
