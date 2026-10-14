import "server-only";

import { compactProviders, type Providers } from "./services";

// The web's ONLY TMDB client (Chat 4 Phase 3; db/051). The worker's twin is
// workers/screening/tmdb.py. TMDB_READ_TOKEN (the v4 read access token, as a
// bearer) is read here and nowhere else (pnpm check:tmdb-token); this module
// is server-only, so it can never reach a client bundle. A missing token or a
// 401 throws TmdbTokenError naming the variable, never its value. Region US,
// language en-US.

const API = "https://api.themoviedb.org/3";
const TOKEN_ENV = "TMDB_READ_TOKEN";

export class TmdbTokenError extends Error {}
export class TmdbError extends Error {}

export type Kind = "movie" | "tv";
export const KINDS: readonly Kind[] = ["movie", "tv"];

function token(): string {
  const t = process.env.TMDB_READ_TOKEN?.trim();
  if (!t) throw new TmdbTokenError(`${TOKEN_ENV} is not set (server only: Vercel Production and apps/web/.env.local)`);
  return t;
}

async function get(path: string, params: Record<string, string>): Promise<unknown> {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries({ language: "en-US", ...params })) url.searchParams.set(k, v);
  // One retry on 429 (Retry-After, at most 2 s): the web's calls are a few per page.
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token()}`, Accept: "application/json" },
      cache: "no-store",
    });
    if (res.status === 401) throw new TmdbTokenError(`TMDB rejected ${TOKEN_ENV} (HTTP 401 on ${path})`);
    if (res.status === 429 && attempt === 0) {
      const wait = Math.min(Number(res.headers.get("retry-after") ?? "1") || 1, 2);
      await new Promise((r) => setTimeout(r, wait * 1000));
      continue;
    }
    if (res.status === 404) return null;
    if (!res.ok) throw new TmdbError(`TMDB ${path} answered HTTP ${res.status}`);
    return res.json();
  }
  throw new TmdbError(`TMDB rate limit on ${path}`);
}

export type SearchHit = { tmdbId: number; kind: Kind; name: string; year: number | null; posterPath: string | null };

type Raw = Record<string, unknown>;

function yearOf(r: Raw): number | null {
  const d = String(r.release_date ?? r.first_air_date ?? "");
  return /^\d{4}/.test(d) ? Number(d.slice(0, 4)) : null;
}

/** Movies and TV for a query (people dropped), TMDB's order, at most `limit`. */
export async function searchTitles(query: string, limit = 12): Promise<SearchHit[]> {
  const q = query.trim();
  if (q.length < 2) return [];
  const data = (await get("/search/multi", { query: q, region: "US", include_adult: "false" })) as { results?: Raw[] } | null;
  return (data?.results ?? [])
    .filter((r) => r.media_type === "movie" || r.media_type === "tv")
    .slice(0, limit)
    .map((r) => ({
      tmdbId: Number(r.id), kind: r.media_type as Kind, name: String(r.title ?? r.name ?? ""),
      year: yearOf(r), posterPath: typeof r.poster_path === "string" ? r.poster_path : null,
    }));
}

export type TitleDetails = {
  tmdbId: number;
  kind: Kind;
  name: string;
  /** US release date (a movie's earliest US theatrical, else digital, else limited; TV's first air date). */
  releaseDate: string | null;
  posterPath: string | null;
  overview: string | null;
  genres: string[];
  providers: Providers;
  trailerKey: string | null;
  nextEpisodeDate: string | null;
  status: string | null;
};

function usReleaseDate(r: Raw): string | null {
  if (!("release_dates" in r)) return typeof r.first_air_date === "string" && r.first_air_date ? r.first_air_date : null;
  const us = ((r.release_dates as { results?: Raw[] })?.results ?? []).filter((x) => x.iso_3166_1 === "US");
  const byType = new Map<number, string[]>();
  for (const x of us) {
    for (const d of (x.release_dates as Raw[]) ?? []) {
      const day = String(d.release_date ?? "").slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(day)) byType.set(Number(d.type), [...(byType.get(Number(d.type)) ?? []), day]);
    }
  }
  for (const t of [3, 4, 2]) {
    const days = byType.get(t);
    if (days?.length) return days.sort()[0];
  }
  return typeof r.release_date === "string" && r.release_date ? r.release_date : null;
}

function trailerKey(r: Raw): string | null {
  const vids = (((r.videos as { results?: Raw[] })?.results) ?? []).filter((v) => v.site === "YouTube" && typeof v.key === "string");
  const newest = (pred: (v: Raw) => boolean) =>
    vids.filter(pred).sort((a, b) => String(b.published_at ?? "").localeCompare(String(a.published_at ?? "")))[0];
  const v = newest((x) => x.type === "Trailer" && !!x.official) ?? newest((x) => x.type === "Trailer") ?? newest((x) => x.type === "Teaser");
  return v ? String(v.key) : null;
}

/** One title, with its videos and US providers; null when TMDB has none. */
export async function titleDetails(kind: Kind, tmdbId: number): Promise<TitleDetails | null> {
  if (!KINDS.includes(kind) || !Number.isSafeInteger(tmdbId) || tmdbId < 1) return null;
  const append = kind === "movie" ? "videos,watch/providers,release_dates" : "videos,watch/providers";
  const r = (await get(`/${kind}/${tmdbId}`, { append_to_response: append })) as Raw | null;
  if (!r) return null;
  const next = kind === "tv" ? (r.next_episode_to_air as Raw | null) : null;
  const us = ((r["watch/providers"] as { results?: Record<string, Raw> })?.results ?? {}).US;
  return {
    tmdbId, kind, name: String(r.title ?? r.name ?? ""), releaseDate: usReleaseDate(r),
    posterPath: typeof r.poster_path === "string" ? r.poster_path : null,
    overview: typeof r.overview === "string" && r.overview ? r.overview : null,
    genres: ((r.genres as Raw[]) ?? []).map((g) => String(g.name ?? "")).filter(Boolean),
    providers: compactProviders(us),
    trailerKey: trailerKey(r),
    nextEpisodeDate: next && typeof next.air_date === "string" ? next.air_date : null,
    status: typeof r.status === "string" ? r.status : null,
  };
}
