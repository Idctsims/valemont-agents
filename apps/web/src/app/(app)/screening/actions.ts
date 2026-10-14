"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { SHELVES, isKind, type Shelf } from "@/lib/screening/types";
import { createClient } from "@/lib/supabase/server";
import { titleDetails } from "@/lib/tmdb/client";

// The shelves (db/051): every move is a NEW watch_status row (append-only;
// the database refuses UPDATE and DELETE, and sets finished_at itself on a
// move to finished). Written through the owner's session (RLS). A title goes
// into `titles` the first time it is shelved, from TMDB (server side); the
// worker refreshes its providers daily. Returns an ActionResult and never
// throws an expected failure (src/lib/action-result.ts).

const SHELF_NAMES = new Set<string>(SHELVES.map((s) => s.shelf));

function checkShelf(shelf: string): Shelf {
  if (!SHELF_NAMES.has(shelf)) throw new Refusal("Unknown shelf.");
  return shelf as Shelf;
}

async function move(titleId: number, shelf: Shelf) {
  const supabase = await createClient();
  const cur = await supabase.from("v_watch_current").select("status").eq("title_id", titleId).maybeSingle();
  if (cur.error) throw new Refusal(`Couldn't read the shelf: ${cur.error.message}`);
  if ((cur.data as { status?: string } | null)?.status === shelf) return; // already there: nothing to write
  const { error } = await supabase.from("watch_status").insert({ title_id: titleId, status: shelf });
  if (error) {
    if (error.code === "23503") throw new Refusal("That title is gone.");
    throw new Refusal(`Couldn't move it: ${error.message}`);
  }
}

/** Put a TMDB title on a shelf: add it to `titles` first if it is new. */
export async function putOnShelf(input: { kind: string; tmdbId: number; shelf: string }): Promise<ActionResult<{ titleId: number }>> {
  return settle(async () => {
    await requireOwner();
    const shelf = checkShelf(input.shelf);
    const tmdbId = Number(input.tmdbId);
    if (!isKind(input.kind) || !Number.isSafeInteger(tmdbId) || tmdbId < 1) throw new Refusal("Unknown title.");
    const supabase = await createClient();
    const found = await supabase.from("titles").select("id").eq("kind", input.kind).eq("tmdb_id", tmdbId).maybeSingle();
    if (found.error) throw new Refusal(`Couldn't read the title: ${found.error.message}`);
    let titleId = (found.data as { id: number } | null)?.id;
    if (!titleId) {
      const d = await titleDetails(input.kind, tmdbId);
      if (!d) throw new Refusal("TMDB has no such title.");
      const ins = await supabase.from("titles").insert({
        tmdb_id: d.tmdbId, kind: d.kind, name: d.name.slice(0, 300), release_date: d.releaseDate,
        poster_path: d.posterPath && /^\/[A-Za-z0-9_.-]+$/.test(d.posterPath) ? d.posterPath : null,
        overview: d.overview?.slice(0, 4000) ?? null, genres: d.genres.slice(0, 20), providers: d.providers,
        providers_refreshed_at: new Date().toISOString(),
        trailer_key: d.trailerKey && /^[A-Za-z0-9_-]{6,20}$/.test(d.trailerKey) ? d.trailerKey : null,
        next_episode_date: d.kind === "tv" ? d.nextEpisodeDate : null, status: d.status?.slice(0, 60) ?? null,
      }).select("id").single();
      if (ins.error) {
        // Another tap (or the worker's trailer match) added it first: use that row.
        if (ins.error.code !== "23505") throw new Refusal(`Couldn't add the title: ${ins.error.message}`);
        const again = await supabase.from("titles").select("id").eq("kind", input.kind).eq("tmdb_id", tmdbId).single();
        if (again.error) throw new Refusal(`Couldn't add the title: ${again.error.message}`);
        titleId = (again.data as { id: number }).id;
      } else {
        titleId = (ins.data as { id: number }).id;
      }
    }
    await move(titleId, shelf);
    refresh();
    return { titleId };
  });
}

/** Move a title already in the database to another shelf. */
export async function moveTitle(input: { titleId: number; shelf: string }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    const shelf = checkShelf(input.shelf);
    const titleId = Number(input.titleId);
    if (!Number.isSafeInteger(titleId) || titleId < 1) throw new Refusal("Unknown title.");
    await move(titleId, shelf);
    refresh();
  });
}
