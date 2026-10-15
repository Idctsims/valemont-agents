// The Screening Room's shapes and pure helpers (db/051). No token, no server.

import type { Providers } from "@/lib/tmdb/services";

export type Shelf = "want" | "watching" | "finished";
export const SHELVES: readonly { shelf: Shelf; label: string }[] = [
  { shelf: "watching", label: "Watching" },
  { shelf: "want", label: "Want" },
  { shelf: "finished", label: "Finished" },
];

/** Finished shows only the last 20 (owner, 2026-10-14). */
export const FINISHED_SHOWN = 20;
/** The Upcoming strip's horizon. */
export const UPCOMING_DAYS = 30;

export type ShelfTitle = {
  titleId: number;
  tmdbId: number;
  kind: "movie" | "tv";
  name: string;
  posterPath: string | null;
  releaseDate: string | null;
  nextEpisodeDate: string | null;
  providers: Providers;
  status: Shelf;
  movedAt: string;
  finishedAt: string | null;
  isTest: boolean;
};

/** The owner's date, offset by `days` (America/Chicago), as YYYY-MM-DD. */
export function chicagoDay(days = 0, now: Date = new Date()): string {
  const d = new Date(now.getTime() + days * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

/** "Oct 20" (a stored DATE, printed as written, no timezone shift). */
export function shortDate(day: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${day}T00:00:00Z`));
}

/** "Oct 20, 2026". */
export function longDate(day: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${day}T00:00:00Z`));
}

export function yearOf(day: string | null): string | null {
  return day && /^\d{4}/.test(day) ? day.slice(0, 4) : null;
}

/** `?tab=` on /screening: the shelves (default) or the trailers. */
export type ScreeningTab = "shelves" | "trailers";
export function screeningTabFromParam(raw: string | string[] | undefined): ScreeningTab {
  return (Array.isArray(raw) ? raw[0] : raw) === "trailers" ? "trailers" : "shelves";
}

export function isKind(v: string): v is "movie" | "tv" {
  return v === "movie" || v === "tv";
}

/** `?view=` on the Trailers tab (Phase 4): For you (default, ranked by the
 * screening profile) or Latest (newest first). */
export type TrailerView = "for_you" | "latest";
export function trailerViewFromParam(raw: string | string[] | undefined): TrailerView {
  return (Array.isArray(raw) ? raw[0] : raw) === "latest" ? "latest" : "for_you";
}
