import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import {
  PAGE_SIZE, hides, otherOutlets, type FeedCategory, type FeedStory, type Pillar, type PublishedPrecision, type Sport,
} from "./types";

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
  /** v_feed_stories (db/045); feed_for_you returns it as `official`. */
  source_official?: boolean | null;
  /** v_feed_stories (db/048); feed_for_you returns it as `published_precision`. */
  lead_published_precision?: PublishedPrecision | null;
};

type TestRow = {
  id: number;
  title: string;
  summary: string | null;
  why_it_matters: string | null;
  item_count: number;
  is_test: boolean;
  lead_item_id: number;
  lead: {
    url: string | null; published_at: string; published_precision: PublishedPrecision | null;
    source: { name: string; official: boolean } | null;
  } | null;
};

type ForYouRow = ViewRow & {
  pos: number; reaction: string | null; floored: boolean | null; cross_pillar: boolean; official: boolean | null;
  published_precision: PublishedPrecision | null;
};

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
  opts: { pillar: Pillar; page: number; withTests?: boolean; sport?: Sport | null; category?: FeedCategory | null },
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
    let q = supabase
      .from("stories")
      .select("id, title, summary, why_it_matters, item_count, is_test, lead_item_id, lead:content_items!stories_lead_item_fk(url, published_at, published_precision, source:sources(name, official))")
      .contains("pillars", [opts.pillar])
      .is("filtered_reason", null)
      .not("lead_item_id", "is", null)
      .not("id", "in", notHidden);
    if (opts.sport) q = q.eq("sport", opts.sport);
    if (opts.category) q = q.eq("category", opts.category);
    const { data, error } = await q
      .order("first_seen_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, to);
    if (error) throw new Error(`Couldn't read the feed: ${error.message}`);
    const rows = ((data ?? []) as unknown as TestRow[]).filter((r) => r.lead?.url);
    base = rows.map((r) => ({
      id: r.id, title: r.title, summary: r.summary, sourceName: r.lead!.source?.name ?? "Unknown",
      url: r.lead!.url!, publishedAt: r.lead!.published_at, itemCount: r.item_count, isTest: r.is_test,
      whyItMatters: r.why_it_matters, official: !!r.lead!.source?.official,
      publishedPrecision: r.lead!.published_precision ?? "time",
    }));
    meta = rows.map((r) => ({ id: r.id, leadItemId: r.lead_item_id, sourceName: r.lead!.source?.name ?? "Unknown", itemCount: r.item_count }));
  } else {
    let q = supabase
      .from("v_feed_stories")
      .select("id, title, summary, why_it_matters, item_count, lead_item_id, lead_url, lead_published_at, source_name, source_official, lead_published_precision")
      .contains("pillars", [opts.pillar])
      .not("id", "in", notHidden);
    if (opts.sport) q = q.eq("sport", opts.sport);
    if (opts.category) q = q.eq("category", opts.category);
    const { data, error } = await q
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

function viewStory(r: ViewRow & { official?: boolean | null; published_precision?: PublishedPrecision | null }): Base {
  return {
    id: r.id, title: r.title, summary: r.summary, sourceName: r.source_name, url: r.lead_url,
    publishedAt: r.lead_published_at, itemCount: r.item_count, isTest: false, whyItMatters: r.why_it_matters,
    official: !!(r.source_official ?? r.official),
    publishedPrecision: r.lead_published_precision ?? r.published_precision ?? "time",
  };
}

function viewMeta(r: ViewRow): Meta {
  return { id: r.id, leadItemId: r.lead_item_id, sourceName: r.source_name, itemCount: r.item_count };
}

/** One page of For you: db/043 feed_for_you() (the learned score minus
 * recency plus any event boost, the capped Alabama floor, at most 5 tier-1
 * sports stories on Home's page one, 6 per tier-1 follow on Sports'; a chip
 * filters before ranking; dismissed stories already left out). Never shows a
 * test story. p_sport / p_category are sent only when a chip is chosen (both
 * default to NULL). `crossCap` (p_cross_cap): the tier-1 sports cross-ins
 * allowed on page one, floored ones first; 0 leaves them all out (/news is
 * Home's pillar only); omitted, the default 5. */
export async function forYouStories(
  supabase: SupabaseClient,
  opts: { pillar: Pillar; page: number; sport?: Sport | null; category?: FeedCategory | null; crossCap?: number },
): Promise<{ stories: FeedStory[]; hasMore: boolean }> {
  const { data, error } = await supabase.rpc("feed_for_you", {
    p_pillar: opts.pillar,
    p_limit: PAGE_SIZE + 1,
    p_offset: (opts.page - 1) * PAGE_SIZE,
    ...(opts.sport ? { p_sport: opts.sport } : {}),
    ...(opts.category ? { p_category: opts.category } : {}),
    ...(opts.crossCap !== undefined ? { p_cross_cap: opts.crossCap } : {}),
  });
  if (error) throw new Error(`Couldn't read For you: ${error.message}`);
  const rows = (data ?? []) as ForYouRow[];
  const loved = new Set(rows.filter((r) => r.reaction === "love").map((r) => r.id));
  return finish(supabase, opts.pillar, rows.map(viewStory), rows.map(viewMeta), loved);
}

/** One tech story by id, for the free-credits push's deep link
 * (/tech/story/[id]): real, kept, linkable, pillar tech (v_feed_stories), or
 * null. Its Love/Neutral/Not interested write pillar tech. */
export async function techStory(supabase: SupabaseClient, id: number): Promise<FeedStory | null> {
  const { data, error } = await supabase
    .from("v_feed_stories")
    .select("id, title, summary, why_it_matters, item_count, lead_item_id, lead_url, lead_published_at, source_name, source_official, lead_published_precision")
    .eq("id", id)
    .contains("pillars", ["tech"])
    .maybeSingle();
  if (error) throw new Error(`Couldn't read the story: ${error.message}`);
  if (!data) return null;
  const row = data as ViewRow;
  const { stories } = await finish(supabase, "tech", [viewStory(row)], [viewMeta(row)]);
  return stories[0] ?? null;
}

// ------------------------------------------------------------ /settings/feed: tech sources

export type TechSourceRow = {
  slug: string;
  name: string;
  kind: string;
  official: boolean;
  active: boolean;
  lastOkAt: string | null;
  lastStatus: "ok" | "not_modified" | "error" | null;
  lastError: string | null;
  failures: number;
  lastItemAt: string | null;
  items24h: number;
  notes: string | null;
};

/** Every Tech Hub source's health (db/045): last good fetch, newest item,
 * failures in a row. Official first, then by name. */
export async function techSourcesHealth(supabase: SupabaseClient): Promise<TechSourceRow[]> {
  const [sources, stats] = await Promise.all([
    supabase
      .from("sources")
      .select("id, slug, name, kind, official, active, last_ok_at, last_status, last_error, consecutive_failures, notes")
      .eq("is_test", false)
      .contains("pillars", ["tech"])
      .order("official", { ascending: false })
      .order("name"),
    supabase.from("v_feed_source_stats").select("source_id, items_24h, last_item_at"),
  ]);
  for (const r of [sources, stats]) {
    if (r.error) throw new Error(`Couldn't read the tech sources: ${r.error.message}`);
  }
  const byId = new Map(
    ((stats.data ?? []) as { source_id: number; items_24h: number; last_item_at: string | null }[]).map((s) => [s.source_id, s]),
  );
  type Db = {
    id: number; slug: string; name: string; kind: string; official: boolean; active: boolean; last_ok_at: string | null;
    last_status: TechSourceRow["lastStatus"]; last_error: string | null; consecutive_failures: number; notes: string | null;
  };
  return ((sources.data ?? []) as Db[]).map((s) => ({
    slug: s.slug, name: s.name, kind: s.kind, official: s.official, active: s.active, lastOkAt: s.last_ok_at,
    lastStatus: s.last_status, lastError: s.last_error, failures: s.consecutive_failures,
    lastItemAt: byId.get(s.id)?.last_item_at ?? null, items24h: Number(byId.get(s.id)?.items_24h ?? 0), notes: s.notes,
  }));
}

// ------------------------------------------------------------ /settings/sources

export type SourceRow = {
  id: number;
  slug: string;
  name: string;
  pillars: string[];
  kind: "rss" | "gnews" | "page" | "hn";
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

// ------------------------------------------- /settings/feed: moves and boosts

export type MoveKind = "trade" | "signing" | "release" | "retirement";

export type MoveProposal = {
  id: number;
  person: string;
  kind: MoveKind;
  from: string | null;
  to: string | null;
  storyTitle: string | null;
  isTest: boolean;
  createdAt: string;
  decision: "confirmed" | "dismissed" | null;
  decidedAt: string | null;
  /** Who the person is with now (v_entity_links_current); null: no one. */
  currentWith: string | null;
};

type ProposalRowDb = {
  id: number; kind: MoveKind; is_test: boolean; created_at: string; decision: "confirmed" | "dismissed" | null;
  decided_at: string | null; person_entity_id: number;
  person: { name: string } | null; from: { name: string } | null; to: { name: string } | null;
  story: { title: string } | null;
};

/** How many decided proposals the page keeps showing under the open ones. */
const DECIDED_SHOWN = 8;

/** The summarizer's move proposals (db/042): open ones first, then the latest
 * decided. Test proposals (e2e) are shown, tagged, only to a request whose
 * e2e marker the server verified (`withTests`), pending and decided alike:
 * the same rule as test stories (owner, 2026-10-13). */
export async function moveProposals(
  supabase: SupabaseClient,
  opts: { withTests?: boolean } = {},
): Promise<MoveProposal[]> {
  const cols = "id, kind, is_test, created_at, decision, decided_at, person_entity_id, " +
    "person:entities!entity_link_proposals_person_entity_id_fkey(name), " +
    "from:entities!entity_link_proposals_from_entity_id_fkey(name), " +
    "to:entities!entity_link_proposals_to_entity_id_fkey(name), story:stories(title)";
  let openQ = supabase.from("entity_link_proposals").select(cols).is("decision", null);
  let decidedQ = supabase.from("entity_link_proposals").select(cols).not("decision", "is", null);
  if (!opts.withTests) {
    openQ = openQ.eq("is_test", false);
    decidedQ = decidedQ.eq("is_test", false);
  }
  const [open, decided] = await Promise.all([
    openQ.order("created_at", { ascending: false }),
    decidedQ.order("decided_at", { ascending: false }).limit(DECIDED_SHOWN),
  ]);
  for (const r of [open, decided]) {
    if (r.error) throw new Error(`Couldn't read the proposed changes: ${r.error.message}`);
  }
  const rows = [...((open.data ?? []) as unknown as ProposalRowDb[]), ...((decided.data ?? []) as unknown as ProposalRowDb[])];
  const persons = [...new Set(rows.map((r) => r.person_entity_id))];
  const current = new Map<number, string>();
  if (persons.length) {
    const { data, error } = await supabase.from("v_entity_links_current").select("from_entity_id, to_entity_id").in("from_entity_id", persons);
    if (error) throw new Error(`Couldn't read the current links: ${error.message}`);
    const links = (data ?? []) as { from_entity_id: number; to_entity_id: number }[];
    if (links.length) {
      const names = await supabase.from("entities").select("id, name").in("id", links.map((l) => l.to_entity_id));
      if (names.error) throw new Error(`Couldn't read the current links: ${names.error.message}`);
      const byId = new Map(((names.data ?? []) as { id: number; name: string }[]).map((e) => [e.id, e.name]));
      for (const l of links) current.set(l.from_entity_id, byId.get(l.to_entity_id) ?? "Unknown");
    }
  }
  return rows.map((r) => ({
    id: r.id, person: r.person?.name ?? "Unknown", kind: r.kind, from: r.from?.name ?? null, to: r.to?.name ?? null,
    storyTitle: r.story?.title ?? null, isTest: r.is_test, createdAt: r.created_at, decision: r.decision,
    decidedAt: r.decided_at, currentWith: current.get(r.person_entity_id) ?? null,
  }));
}

export type BoostNow = { label: string; sport: string; endsAt: string };

/** Event windows active at `nowIso` (db/042 boost_windows). */
export async function boostsNow(supabase: SupabaseClient, nowIso: string): Promise<BoostNow[]> {
  const { data, error } = await supabase
    .from("boost_windows")
    .select("label, sport, ends_at")
    .lte("starts_at", nowIso)
    .gt("ends_at", nowIso)
    .order("ends_at");
  if (error) throw new Error(`Couldn't read the boosts: ${error.message}`);
  return ((data ?? []) as { label: string; sport: string; ends_at: string }[]).map((b) => ({ label: b.label, sport: b.sport, endsAt: b.ends_at }));
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

export type FollowSummary = {
  name: string; kind: string; tier: 1 | 2 | 3; pillar: Pillar | null; floor: boolean; sport: string | null;
  /** A derived follow (owner D3, db/042): the current team of this tier-1 person. */
  via?: string;
};

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
    supabase.from("follows").select("tier, pillar, floor, entity:entities(id, name, kind, sport, is_test)").order("tier"),
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
  type FollowDb = { tier: 1 | 2 | 3; pillar: Pillar | null; floor: boolean; entity: { id: number; name: string; kind: string; sport: string | null; is_test: boolean } | null };
  const raw = ((follows.data ?? []) as unknown as FollowDb[]).filter((f) => f.entity && !f.entity.is_test);
  const followRows: FollowSummary[] = raw.map((f) => ({
    name: f.entity!.name, kind: f.entity!.kind, sport: f.entity!.sport, tier: f.tier, pillar: f.pillar, floor: f.floor,
  }));
  // Owner D3 (db/042): each tier-1 person's current team, tier 2 on Sports,
  // as the worker derives it (ledger.FEED_FOLLOWS_SQL).
  const people = raw.filter((f) => f.tier === 1 && f.entity!.kind === "person");
  if (people.length) {
    const links = await supabase.from("v_entity_links_current").select("from_entity_id, to_entity_id")
      .in("from_entity_id", people.map((f) => f.entity!.id));
    if (links.error) throw new Error(`Couldn't read the feed profile: ${links.error.message}`);
    const rows = (links.data ?? []) as { from_entity_id: number; to_entity_id: number }[];
    if (rows.length) {
      const teams = await supabase.from("entities").select("id, name, kind, sport").in("id", rows.map((l) => l.to_entity_id));
      if (teams.error) throw new Error(`Couldn't read the feed profile: ${teams.error.message}`);
      const byId = new Map(((teams.data ?? []) as { id: number; name: string; kind: string; sport: string | null }[]).map((t) => [t.id, t]));
      const personName = new Map(people.map((f) => [f.entity!.id, f.entity!.name]));
      const seen = new Map<string, FollowSummary>();
      for (const l of rows) {
        const t = byId.get(l.to_entity_id);
        if (!t) continue;
        const prev = seen.get(t.name);
        const via = personName.get(l.from_entity_id)!;
        if (prev) prev.via = `${prev.via} and ${via}`;
        else seen.set(t.name, { name: t.name, kind: t.kind, sport: t.sport, tier: 2, pillar: "sports", floor: false, via });
      }
      followRows.push(...seen.values());
    }
  }
  followRows.sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name));
  return { latest, history, follows: followRows, pendingReactions: pending };
}
