import { z } from "zod";

// The Screening Room's "More like…" (owner, Nov 21; docs/screening.md §8).
// The owner names an anchor (a film, a series, a person, or a phrase);
// Sonnet names titles with the same vibe; each is matched on TMDB and the
// unmatched are dropped. Pure: no I/O. The route is /api/screening/vibe.

export const ASK_PICKS = 15;
export const MAX_CARDS = 15;
export const CACHE_DAYS = 30;
export const RATE_LIMIT = 10;
export const RATE_WINDOW_S = 60;
export const VIBE_MODEL = "claude-sonnet-5-5";
export const MAX_TEXT = 120;

export type Anchor = {
  kind: "movie" | "tv" | "person" | "text";
  id?: number;
  label: string;
  year?: number | null;
  /** A person's department on TMDB ("Directing", "Acting"). */
  dept?: string | null;
};

export const vibePickSchema = z.object({
  title: z.string().min(1).max(200).describe("The title as TMDB lists it, in English."),
  year: z.number().int().min(1900).max(2100).nullable().describe("Release year (a series: its first year)."),
  kind: z.enum(["movie", "tv"]).describe("movie for a film, tv for a series."),
  reason: z.string().min(1).max(120).describe("Why it shares the vibe, at most 12 words."),
});
export type VibePick = z.infer<typeof vibePickSchema>;
export const vibeSchema = z.object({ picks: z.array(vibePickSchema) });

export type Candidate = {
  tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; popularity: number; posterPath: string | null;
};
export type VibeCard = {
  tmdbId: number; kind: "movie" | "tv"; name: string; year: number | null; posterPath: string | null; reason: string;
};

function collapse(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

/** What was searched, as db/088's anchor_key: the TMDB id, or the phrase lowercased. */
export function anchorKey(a: Anchor): string {
  if (a.kind !== "text" && a.id != null) return `${a.kind}:${a.id}`;
  return `text:${collapse(a.label).toLowerCase().slice(0, MAX_TEXT)}`;
}

/** A stored anchor_key back to an anchor (a recent-search chip; its key re-finds the stored row). */
export function anchorFromKey(key: string, label: string): Anchor | null {
  const m = /^(movie|tv|person):(\d+)$/.exec(key);
  if (m) {
    // "Gladiator (2000)" was stored for a title: the chip keeps the name; the key does the finding.
    const name = m[1] === "person" ? label : label.replace(/\s\(\d{4}\)$/, "");
    return { kind: m[1] as Anchor["kind"], id: Number(m[2]), label: name };
  }
  if (key.startsWith("text:") && key.length > 6) return { kind: "text", label };
  return null;
}

/** The label shown for a search: "Gladiator (2000)", "Ridley Scott", or the phrase. */
export function anchorLabel(a: Anchor): string {
  const name = collapse(a.label).slice(0, 180);
  return (a.kind === "movie" || a.kind === "tv") && a.year ? `${name} (${a.year})` : name;
}

/** Names compared without case, accents or punctuation: "Ben-Hur" = "Ben Hur". */
export function sameName(a: string, b: string): boolean {
  const n = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase()
    .replace(/['’]/g, "").replace(/&/g, "and").replace(/[^a-z0-9]+/g, " ").trim();
  return n(a) === n(b);
}

/** A pick's TMDB title: same kind and name, the exact year first, else within a year; the most popular of those. */
export function matchPick(p: VibePick, hits: readonly Candidate[]): Candidate | null {
  const named = hits.filter((h) => h.kind === p.kind && sameName(h.name, p.title));
  const best = (xs: Candidate[]) => xs.sort((a, b) => b.popularity - a.popularity)[0] ?? null;
  if (p.year == null) return best([...named]);
  return best(named.filter((h) => h.year === p.year))
    ?? best(named.filter((h) => h.year != null && Math.abs(h.year - p.year!) <= 1));
}

/** The shown list: matched picks in Claude's order, the anchor itself out, each title once, at most `max`. */
export function finalize(rows: readonly { pick: VibePick; hit: Candidate | null }[], a: Anchor, max = MAX_CARDS): VibeCard[] {
  const seen = new Set<string>();
  if (a.kind === "movie" || a.kind === "tv") seen.add(`${a.kind}:${a.id}`);
  const out: VibeCard[] = [];
  for (const { pick, hit } of rows) {
    if (!hit) continue;
    const key = `${hit.kind}:${hit.tmdbId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ tmdbId: hit.tmdbId, kind: hit.kind, name: hit.name, year: hit.year, posterPath: hit.posterPath, reason: pick.reason });
    if (out.length >= max) break;
  }
  return out;
}

export function vibeSystem(): string {
  return [
    "You recommend films and TV series by vibe for one person's Screening Room.",
    `Given what they typed (a film, a series, a person, or a description), name ${ASK_PICKS} titles that share its feel:`,
    "tone, era, scale, genre, setting, the people who made it or star in it.",
    "Lead with the closest matches, then a few less obvious ones. Films and series both count.",
    "Never include the title they typed itself. For a person, include their own strongest work and work like it.",
    "Real, released or announced titles only, named exactly as TMDB lists them in English, with the release year",
    "(a series: its first year). Each reason is at most 12 words, specific: the shared writer, star, era, or feeling.",
  ].join(" ");
}

export function vibePrompt(a: Anchor): string {
  switch (a.kind) {
    case "movie":
      return `More like the film ${anchorLabel(a)}.`;
    case "tv":
      return `More like the series ${anchorLabel(a)}.`;
    case "person":
      return `More like the work of ${collapse(a.label)}${a.dept ? ` (${a.dept})` : ""}.`;
    default:
      return `More like this: ${collapse(a.label).slice(0, MAX_TEXT)}`;
  }
}

/** A typed anchor from a request body, or a reason it isn't one. */
export function parseAnchor(raw: unknown): Anchor | string {
  if (!raw || typeof raw !== "object") return "Type a title, a person or a mood first.";
  const o = raw as Record<string, unknown>;
  const kind = o.kind;
  const label = typeof o.label === "string" ? collapse(o.label) : "";
  if (kind !== "movie" && kind !== "tv" && kind !== "person" && kind !== "text") return "That search didn't make sense. Try again.";
  if (label.length < 2) return "Type at least two letters.";
  if (kind === "text") {
    if (label.length > MAX_TEXT) return `Keep it under ${MAX_TEXT} characters.`;
    return { kind, label };
  }
  const id = Number(o.id);
  if (!Number.isSafeInteger(id) || id < 1 || id > 9_999_999_999) return "That search didn't make sense. Try again.";
  const year = Number.isSafeInteger(Number(o.year)) && Number(o.year) > 1800 ? Number(o.year) : null;
  const dept = typeof o.dept === "string" && o.dept.length <= 40 ? o.dept : null;
  return { kind, id, label: label.slice(0, 180), year, dept };
}
