// The feed's shapes and pure helpers, shared by the Home "Latest" list,
// /settings/sources and the unit specs. The worker writes every row (db/032);
// the web only reads, through RLS.

export const PAGE_SIZE = 30;
/** Older pages than this are not offered: the feed is for now, not an archive. */
export const MAX_PAGE = 20;

export type Outlet = { name: string; url: string };

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
};

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

/** `?latest=` as a page number, 1 to MAX_PAGE; anything else is page 1. */
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
