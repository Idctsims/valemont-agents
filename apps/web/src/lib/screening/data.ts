import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { Kind } from "@/lib/tmdb/client";
import type { Providers } from "@/lib/tmdb/services";

import { FINISHED_SHOWN, UPCOMING_DAYS, chicagoDay, type Shelf, type ShelfTitle } from "./types";

// Reads only, through the owner's session (RLS, db/051). The shelves are the
// latest move per title (v_watch_current over the append-only watch_status);
// a test title (is_test) shows only to a request with the verified e2e marker.

type CurrentRow = { title_id: number; status: Shelf; finished_at: string | null; moved_at: string; is_test: boolean };
type TitleRow = {
  id: number; tmdb_id: number; kind: Kind; name: string; poster_path: string | null; release_date: string | null;
  next_episode_date: string | null; providers: Providers | null; is_test: boolean;
};

const TITLE_COLS = "id, tmdb_id, kind, name, poster_path, release_date, next_episode_date, providers, is_test";

function toShelf(c: CurrentRow, t: TitleRow): ShelfTitle {
  return {
    titleId: t.id, tmdbId: t.tmdb_id, kind: t.kind, name: t.name, posterPath: t.poster_path,
    releaseDate: t.release_date, nextEpisodeDate: t.next_episode_date, providers: t.providers ?? {},
    status: c.status, movedAt: c.moved_at, finishedAt: c.finished_at, isTest: t.is_test,
  };
}

/** Watching, Want (newest move first) and Finished (the last FINISHED_SHOWN). */
export async function shelves(supabase: SupabaseClient, opts: { withTests?: boolean } = {}) {
  let q = supabase.from("v_watch_current").select("title_id, status, finished_at, moved_at, is_test");
  if (!opts.withTests) q = q.eq("is_test", false);
  const { data, error } = await q.order("moved_at", { ascending: false });
  if (error) throw new Error(`Couldn't read the shelves: ${error.message}`);
  const current = (data ?? []) as CurrentRow[];
  const titles = new Map<number, TitleRow>();
  if (current.length) {
    const t = await supabase.from("titles").select(TITLE_COLS).in("id", current.map((c) => c.title_id));
    if (t.error) throw new Error(`Couldn't read the titles: ${t.error.message}`);
    for (const r of (t.data ?? []) as TitleRow[]) titles.set(r.id, r);
  }
  const rows = current.filter((c) => titles.has(c.title_id)).map((c) => toShelf(c, titles.get(c.title_id)!));
  const finished = rows.filter((r) => r.status === "finished")
    .sort((a, b) => (b.finishedAt ?? "").localeCompare(a.finishedAt ?? ""));
  return {
    watching: rows.filter((r) => r.status === "watching"),
    want: rows.filter((r) => r.status === "want"),
    finished: finished.slice(0, FINISHED_SHOWN),
    finishedTotal: finished.length,
    all: rows,
  };
}

export type UpcomingRow = ShelfTitle & { on: string; what: "release" | "episode" };

/** Want and Watching titles with a US release or a next episode in the next
 * UPCOMING_DAYS days (Chicago dates), soonest first. */
export function upcoming(rows: ShelfTitle[], today = chicagoDay()): UpcomingRow[] {
  const last = chicagoDay(UPCOMING_DAYS);
  const out: UpcomingRow[] = [];
  for (const r of rows) {
    if (r.status === "finished") continue;
    const ep = r.nextEpisodeDate && r.nextEpisodeDate >= today && r.nextEpisodeDate <= last ? r.nextEpisodeDate : null;
    const rel = r.releaseDate && r.releaseDate >= today && r.releaseDate <= last ? r.releaseDate : null;
    const on = ep ?? rel;
    if (on) out.push({ ...r, on, what: ep ? "episode" : "release" });
  }
  return out.sort((a, b) => a.on.localeCompare(b.on) || a.name.localeCompare(b.name));
}

/** A title's row and its current shelf, if it is in the database. */
export async function titleOnShelf(supabase: SupabaseClient, kind: Kind, tmdbId: number, opts: { withTests?: boolean } = {}) {
  let q = supabase.from("titles").select(TITLE_COLS).eq("kind", kind).eq("tmdb_id", tmdbId);
  if (!opts.withTests) q = q.eq("is_test", false);
  const { data, error } = await q.maybeSingle();
  if (error) throw new Error(`Couldn't read the title: ${error.message}`);
  if (!data) return null;
  const t = data as TitleRow;
  const cur = await supabase.from("v_watch_current").select("title_id, status, finished_at, moved_at, is_test").eq("title_id", t.id).maybeSingle();
  if (cur.error) throw new Error(`Couldn't read the shelf: ${cur.error.message}`);
  return { title: t, status: ((cur.data as CurrentRow | null)?.status ?? null) as Shelf | null };
}
