// Who made a title and how long it runs, from a TMDB details object (the
// Letterboxd-style title page, owner 2026-11-22; docs/screening.md §9).
// Pure: no token, safe anywhere.

type Raw = Record<string, unknown>;

/** "Directed by" a film's directors (at most two) or "Created by" a series' creators; null when TMDB has none. */
export function makersOf(kind: "movie" | "tv", r: Raw): { label: string; names: string[] } | null {
  const names = (rows: unknown, pick: (x: Raw) => boolean) =>
    [...new Set((Array.isArray(rows) ? rows : [])
      .filter((x): x is Raw => !!x && typeof x === "object" && pick(x as Raw))
      .map((x) => String(x.name ?? "").trim()).filter(Boolean))].slice(0, 2);
  if (kind === "tv") {
    const n = names(r.created_by, () => true);
    return n.length ? { label: "Created by", names: n } : null;
  }
  const credits = r.credits && typeof r.credits === "object" ? (r.credits as Raw) : {};
  const n = names(credits.crew, (x) => x.job === "Director");
  return n.length ? { label: "Directed by", names: n } : null;
}

/** A film's runtime, or a series' first listed episode length, in minutes; null when unknown. */
export function runtimeOf(kind: "movie" | "tv", r: Raw): number | null {
  const v = kind === "movie" ? r.runtime : Array.isArray(r.episode_run_time) ? r.episode_run_time[0] : null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}
