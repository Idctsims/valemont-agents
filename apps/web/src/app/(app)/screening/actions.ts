"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { SHELVES, isKind, type Shelf } from "@/lib/screening/types";
import { createClient } from "@/lib/supabase/server";
import { titleDetails } from "@/lib/tmdb/client";
import { tonightServices } from "@/lib/tmdb/services";

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

/** A TMDB title's row in `titles`, added from TMDB (server side) the first
 * time it is shelved or reacted to. Returns its id. */
async function ensureTitle(kind: string, tmdbIdRaw: number): Promise<number> {
  const tmdbId = Number(tmdbIdRaw);
  if (!isKind(kind) || !Number.isSafeInteger(tmdbId) || tmdbId < 1) throw new Refusal("Unknown title.");
  const supabase = await createClient();
  const found = await supabase.from("titles").select("id").eq("kind", kind).eq("tmdb_id", tmdbId).maybeSingle();
  if (found.error) throw new Refusal(`Couldn't read the title: ${found.error.message}`);
  const have = (found.data as { id: number } | null)?.id;
  if (have) return have;
  const d = await titleDetails(kind, tmdbId);
  if (!d) throw new Refusal("TMDB has no such title.");
  const ins = await supabase.from("titles").insert({
    tmdb_id: d.tmdbId, kind: d.kind, name: d.name.slice(0, 300), release_date: d.releaseDate,
    poster_path: d.posterPath && /^\/[A-Za-z0-9_.-]+$/.test(d.posterPath) ? d.posterPath : null,
    overview: d.overview?.slice(0, 4000) ?? null, genres: d.genres.slice(0, 20), providers: d.providers,
    providers_refreshed_at: new Date().toISOString(),
    trailer_key: d.trailerKey && /^[A-Za-z0-9_-]{6,20}$/.test(d.trailerKey) ? d.trailerKey : null,
    next_episode_date: d.kind === "tv" ? d.nextEpisodeDate : null, status: d.status?.slice(0, 60) ?? null,
    // db/053: the owner's services it streams on tonight (the worker's twin: services.py).
    tonight: tonightServices(d.providers),
  }).select("id").single();
  if (!ins.error) return (ins.data as { id: number }).id;
  // Another tap (or the worker) added it first: use that row.
  if (ins.error.code !== "23505") throw new Refusal(`Couldn't add the title: ${ins.error.message}`);
  const again = await supabase.from("titles").select("id").eq("kind", kind).eq("tmdb_id", tmdbId).single();
  if (again.error) throw new Refusal(`Couldn't add the title: ${again.error.message}`);
  return (again.data as { id: number }).id;
}

/** Put a TMDB title on a shelf: add it to `titles` first if it is new. */
export async function putOnShelf(input: { kind: string; tmdbId: number; shelf: string }): Promise<ActionResult<{ titleId: number }>> {
  return settle(async () => {
    await requireOwner();
    const shelf = checkShelf(input.shelf);
    const titleId = await ensureTitle(input.kind, input.tmdbId);
    await move(titleId, shelf);
    refresh();
    return { titleId };
  });
}

// ---------------------------------------------------------------- Phase 4
// Title reactions (db/053 title_reactions, append-only): Love / Neutral /
// Not interested from a For you card or a title page, and the seen-it
// pass's three answers. Each is a NEW row; tapping the pressed reaction
// again clears it ('none'). The database queues the re-rank (debounced);
// the read path drops a reacted title at once.

const REACTIONS = new Set(["love", "neutral", "not_interested", "none"]);
type Target = { titleId: number } | { kind: string; tmdbId: number };

async function targetId(t: Target): Promise<number> {
  if ("titleId" in t) {
    const id = Number(t.titleId);
    if (!Number.isSafeInteger(id) || id < 1) throw new Refusal("Unknown title.");
    return id;
  }
  return ensureTitle(t.kind, t.tmdbId);
}

async function insertReaction(titleId: number, reaction: string, seen: boolean, origin: "card" | "title" | "seen_pass") {
  const supabase = await createClient();
  const { error } = await supabase.from("title_reactions").insert({ title_id: titleId, reaction, seen, origin });
  if (error) {
    if (error.code === "23503") throw new Refusal("That title is gone.");
    throw new Refusal(`Couldn't save it: ${error.message}`);
  }
}

/** Love / Neutral / Not interested (or 'none': take it back) on a title. */
export async function reactToTitle(input: Target & { reaction: string; origin: string }): Promise<ActionResult<{ titleId: number }>> {
  return settle(async () => {
    await requireOwner();
    if (!REACTIONS.has(input.reaction)) throw new Refusal("Unknown reaction.");
    if (input.origin !== "card" && input.origin !== "title") throw new Refusal("Unknown place.");
    const titleId = await targetId(input);
    await insertReaction(titleId, input.reaction, false, input.origin);
    refresh();
    return { titleId };
  });
}

const ANSWERS: Record<string, { reaction: string; seen: boolean }> = {
  seen_loved: { reaction: "love", seen: true },
  seen_not_for_me: { reaction: "not_interested", seen: true },
  not_seen: { reaction: "none", seen: false },
};

/** The seen-it pass: "Seen, loved" / "Seen, not for me" / "Haven't seen". */
export async function answerSeen(input: { titleId: number; answer: string }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    const a = ANSWERS[input.answer];
    if (!a) throw new Refusal("Unknown answer.");
    await insertReaction(await targetId({ titleId: input.titleId }), a.reaction, a.seen, "seen_pass");
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
