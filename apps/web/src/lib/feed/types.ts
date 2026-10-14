// The feed's shapes and pure helpers, shared by the Home "Latest" list,
// /settings/sources and the unit specs. The worker writes every row (db/032);
// the web only reads, through RLS.

export const PAGE_SIZE = 30;
/** Older pages than this are not offered: the feed is for now, not an archive. */
export const MAX_PAGE = 20;

export type Outlet = { name: string; url: string };

export type Pillar = "home" | "sports" | "tech";

/** What a card's buttons and swipes write (db/039 feed_reactions); 'open'
 * comes only from the beacon (/api/feed/open). */
export type CardReaction = "love" | "neutral" | "not_interested" | "undo";
export const CARD_REACTIONS: readonly CardReaction[] = ["love", "neutral", "not_interested", "undo"];

export type FeedStory = {
  id: number;
  title: string;
  /** Haiku's summary; null shows the headline alone. */
  summary: string | null;
  sourceName: string;
  /** The lead item's publisher URL: never news.google.com (db/032). */
  url: string;
  publishedAt: string;
  itemCount: number;
  /** The other outlets carrying the story, one link each. */
  outlets: Outlet[];
  isTest: boolean;
  /** One line tied to a venture, only when the link is concrete (feed_why). */
  whyItMatters: string | null;
  /** Loved in this pillar (the latest reaction): the card stays marked. */
  loved: boolean;
  /** From a first-party source (db/045 sources.official): the "Official" tag. */
  official: boolean;
  /** How precise publishedAt is (db/045, db/048): 'date' when the source
   * states only a day (stored as 00:00 UTC of it), 'none' when undated. */
  publishedPrecision: PublishedPrecision;
  /** A GitHub repo story (db/049 repo_surfacings): what the repo card shows. */
  repo?: RepoInfo | null;
};

/** Where stars_7d came from (db/049; workers/feed/repos.py LABELS). Only
 * `measured` is a measurement: today's snapshot minus ours from 7 days ago. */
export type Stars7dSource = "measured" | "trending_weekly" | "trending_daily" | "since_created" | "age_average";

/** The card's label per source, the worker's LABELS exactly (pinned by
 * e2e/repos-unit.spec.ts). An estimate is never shown as measured. */
export const STARS_7D_LABELS: Readonly<Record<Stars7dSource, string>> = {
  measured: "7d",
  trending_weekly: "trending est.",
  trending_daily: "trending est.",
  since_created: "since created",
  age_average: "est.",
};

export type RepoInfo = {
  fullName: string;
  stars: number | null;
  stars7d: number | null;
  stars7dSource: Stars7dSource | null;
  language: string | null;
  /** At most 3 shown (the card's rule; the row keeps every topic). */
  topics: string[];
};

/** At most this many topics on a repo card. */
export const REPO_TOPICS_SHOWN = 3;

/** "12,345": a star count in full, en-US. */
export function starCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

/** "+340 7d", "+340 trending est.", or null when there is no stars_7d. */
export function stars7dText(repo: Pick<RepoInfo, "stars7d" | "stars7dSource">): string | null {
  if (repo.stars7d === null || repo.stars7dSource === null) return null;
  const sign = repo.stars7d >= 0 ? "+" : "−";
  return `${sign}${starCount(Math.abs(repo.stars7d))} ${STARS_7D_LABELS[repo.stars7dSource]}`;
}

export type PublishedPrecision = "time" | "date" | "none";

/** Home's two orders: the learned ranking (db/039 feed_for_you) and newest first. */
export type FeedTab = "for-you" | "latest";

/** `?feed=` as a tab; anything else is For you. */
export function tabFromParam(raw: string | string[] | undefined): FeedTab {
  return (Array.isArray(raw) ? raw[0] : raw) === "latest" ? "latest" : "for-you";
}

/** Sports News chips (stories.sport). A story with no sport shows under All only. */
export type Sport = "cfb" | "nfl" | "nba" | "f1" | "golf";
export const SPORT_CHIPS: readonly { sport: Sport | null; label: string }[] = [
  { sport: null, label: "All" },
  { sport: "cfb", label: "CFB" },
  { sport: "nfl", label: "NFL" },
  { sport: "nba", label: "NBA" },
  { sport: "f1", label: "F1" },
  { sport: "golf", label: "Golf" },
];

/** `?sport=` as a chip; anything else is All (null). */
export function sportFromParam(raw: string | string[] | undefined): Sport | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return SPORT_CHIPS.some((c) => c.sport === v) ? (v as Sport) : null;
}

/** /news category chips (stories.category, db/042; workers/feed/category.py).
 * A story with no category yet shows under All only. */
export type NewsCategory = "deals" | "hospitality" | "sports_business" | "film_music" | "luxury_marine" | "betting";
export const NEWS_CHIPS: readonly { category: NewsCategory | null; label: string }[] = [
  { category: null, label: "All" },
  { category: "deals", label: "Deals & Capital" },
  { category: "hospitality", label: "Hospitality & Real Estate" },
  { category: "sports_business", label: "Sports Business" },
  { category: "film_music", label: "Film/TV & Music" },
  { category: "luxury_marine", label: "Luxury & Marine" },
  { category: "betting", label: "Betting & Markets" },
];

/** `?category=` as a chip; anything else (including "other") is All. */
export function categoryFromParam(raw: string | string[] | undefined): NewsCategory | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return NEWS_CHIPS.some((c) => c.category === v) ? (v as NewsCategory) : null;
}

/** /tech category chips (stories.category for pillar tech, db/045;
 * workers/feed/tech.py CHIP_CATEGORIES, pinned by e2e/tech-unit.spec.ts).
 * `repos` is GitHub's (Phase 2, db/049); `other` shows under All only. */
export type TechCategory = "models_features" | "pricing_credits" | "tooling" | "agents" | "stack" | "community" | "repos";
export const TECH_CHIPS: readonly { category: TechCategory | null; label: string }[] = [
  { category: null, label: "All" },
  { category: "models_features", label: "Models & features" },
  { category: "pricing_credits", label: "Pricing & credits" },
  { category: "tooling", label: "Tooling" },
  { category: "agents", label: "Agents" },
  { category: "stack", label: "Stack" },
  { category: "community", label: "Community" },
  { category: "repos", label: "Repos" },
];

/** `?category=` on /tech as a chip; anything else is All. */
export function techCategoryFromParam(raw: string | string[] | undefined): TechCategory | null {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return TECH_CHIPS.some((c) => c.category === v) ? (v as TechCategory) : null;
}

/** A chip on either page: a /news or a /tech category. */
export type FeedCategory = NewsCategory | TechCategory;

/** How many For you stories Home shows before "See all news". */
export const HOME_TOP = 10;

/** Tier-1 sports stories allowed into Home's short list (owner, 2026-10-13:
 * at most 2). /news allows none (p_cross_cap 0). workers/feed/rank.py
 * HOME_SHORT_CROSS; tests/test_feed_sports.py pins the two. */
export const HOME_SHORT_CROSS = 2;

/**
 * A feed URL that keeps the chip, the tab and the page, anchored to the
 * stories. Home's old URLs ("/#stories", "/?feed=latest&latest=2#stories")
 * still build the same. News: "/news?category=deals&feed=latest#stories".
 * Sports: "/sports?sport=golf&feed=latest#stories".
 */
export function feedHref(
  base: "/" | "/news" | "/sports" | "/tech",
  o: { tab: FeedTab; page?: number; sport?: Sport | null; category?: FeedCategory | null; anchor?: boolean },
): string {
  const q = new URLSearchParams();
  if (o.sport) q.set("sport", o.sport);
  if (o.category) q.set("category", o.category);
  if (o.tab === "latest") q.set("feed", "latest");
  const page = o.page ?? 1;
  if (page > 1) q.set(o.tab === "latest" ? "latest" : "foryou", String(page));
  const s = q.toString();
  return `${base}${s ? `?${s}` : ""}${o.anchor === false ? "" : "#stories"}`;
}

/** The latest reaction hides a card when it is neutral or not interested. */
export function hides(reaction: string | null | undefined): boolean {
  return reaction === "neutral" || reaction === "not_interested";
}

/** A card's next state after a tap: Love toggles (a second tap is an undo). */
export function nextReaction(tapped: "love" | "neutral" | "not_interested", loved: boolean): CardReaction {
  return tapped === "love" && loved ? "undo" : tapped;
}

/** Swipe distance (px) that commits Love (right) or Not interested (left).
 * Must match --vm-swipe-commit (4.5rem) in tokens.css, as goal rows do. */
export const SWIPE_COMMIT_PX = 72;

export function swipeReaction(dx: number): "love" | "not_interested" | null {
  if (dx >= SWIPE_COMMIT_PX) return "love";
  if (dx <= -SWIPE_COMMIT_PX) return "not_interested";
  return null;
}

/** "12m", "3h", "Yesterday", "Oct 9": how long ago, briefly. */
export function relativeTime(iso: string, now: number): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 24 * 3600) return `${Math.floor(s / 3600)}h`;
  if (s < 48 * 3600) return "Yesterday";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "America/Chicago" }).format(
    new Date(iso),
  );
}

/** A card's time. A date-only item prints its stated day as the source gave
 * it, in UTC ("Oct 6"): its 00:00 UTC timestamp would read as the evening
 * before in Chicago (owner, 2026-10-14). Anything else: relativeTime. */
export function cardWhen(iso: string, precision: PublishedPrecision, now: number): string {
  if (precision !== "date") return relativeTime(iso, now);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));
}

/** "3h ago", "never": for the sources page. */
export function ago(iso: string | null, now: number): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)}m ago`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Other outlets for a story: one per outlet name, the lead's outlet excluded,
 * in the order given (the caller passes them oldest item first). */
export function otherOutlets(leadSource: string, items: { name: string; url: string }[]): Outlet[] {
  const seen = new Set([leadSource]);
  const out: Outlet[] = [];
  for (const it of items) {
    if (seen.has(it.name)) continue;
    seen.add(it.name);
    out.push({ name: it.name, url: it.url });
  }
  return out;
}

/** `?latest=` / `?foryou=` as a page number, 1 to MAX_PAGE; anything else is page 1. */
export function pageFromParam(raw: string | string[] | undefined): number {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n >= 1 && n <= MAX_PAGE ? n : 1;
}

/** 'title_regex:12' -> 'Title matches "box score"'; unknown rules fall back
 * to the raw reason, never hide it. */
export function reasonLabel(reason: string, patterns: Map<number, string>): string {
  const [kind, rest] = reason.split(":", 2);
  const id = Number(rest);
  const pattern = Number.isInteger(id) ? patterns.get(id) : undefined;
  if (kind === "title_regex" && pattern) return `Title matches “${pattern}”`;
  if (kind === "url_regex" && pattern) return `Link matches ${pattern}`;
  if (kind === "drop") return `Haiku: ${rest.replaceAll("_", " ")}`;
  return reason;
}

/** The owner's midnight today (America/Chicago) as an ISO instant. */
export function chicagoMidnight(now: Date = new Date()): string {
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  // The zone's offset at that wall-clock midnight, from the formatter itself.
  const probe = new Date(`${day}T12:00:00Z`);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", timeZoneName: "longOffset" })
    .formatToParts(probe)
    .find((p) => p.type === "timeZoneName")?.value; // "GMT-05:00"
  const m = /GMT([+-])(\d{2}):(\d{2})/.exec(parts ?? "");
  const offset = m ? `${m[1]}${m[2]}:${m[3]}` : "-06:00";
  return new Date(`${day}T00:00:00${offset}`).toISOString();
}
