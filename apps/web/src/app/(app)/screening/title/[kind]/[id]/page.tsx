import type { Metadata } from "next";

import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { TitleView } from "@/components/screening/title-view";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { titleOnShelf } from "@/lib/screening/data";
import { titleReaction } from "@/lib/screening/recs";
import { isKind } from "@/lib/screening/types";
import { createClient } from "@/lib/supabase/server";
import { titleDetails, type TitleDetails } from "@/lib/tmdb/client";
import type { Providers } from "@/lib/tmdb/services";

export const metadata: Metadata = { title: "Screening Room" };

// One title (db/051), laid out as Letterboxd's film page (owner, 2026-11-22;
// docs/screening.md §9): the backdrop across the top with the back arrow and
// "⋯" over it; the name, year and who made it, the trailer button and the
// runtime, the poster at the right; the tagline and overview; the buttons
// (Watch, Watching for a series, Like, Watchlist); More like this. Then only
// the trailer, Tonight on your services and Other ways to watch. Live from
// TMDB (server side) with the owner's record from the database; a test title
// (not on TMDB) renders from its database row, and only with the verified e2e
// marker. In the news and the Arsenal cards left this page (owner, Nov 22).

export default async function TitlePage({ params }: { params: Promise<{ kind: string; id: string }> }) {
  await requireOwner();
  const { kind, id } = await params;
  const tmdbId = Number(id);
  if (!isKind(kind) || !Number.isSafeInteger(tmdbId) || tmdbId < 1) notFound();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [live, shelf] = await Promise.all([titleDetails(kind, tmdbId), titleOnShelf(supabase, kind, tmdbId, { withTests })]);
  const t: TitleDetails | null = live ?? (shelf ? {
    tmdbId, kind, name: shelf.title.name, releaseDate: shelf.title.release_date, posterPath: shelf.title.poster_path,
    overview: null, genres: [], providers: (shelf.title.providers ?? {}) as Providers, trailerKey: null,
    nextEpisodeDate: shelf.title.next_episode_date, status: null, backdropPath: null, tagline: null, runtime: null, makers: null,
  } : null);
  if (!t) notFound();
  // The record: the title's id when it is in `titles` (shelved or reacted to before), its shelf and reaction.
  const known = shelf?.title.id ?? (await titleIdOf(supabase, kind, tmdbId, withTests));
  const reaction = known ? await titleReaction(supabase, known) : null;
  const target = known ? { titleId: known } : { kind: t.kind, tmdbId: t.tmdbId };

  const r = reaction === "love" || reaction === "neutral" || reaction === "not_interested" ? reaction : null;
  return <TitleView t={t} shelf={shelf?.status ?? null} reaction={r} target={target} />;
}

/** A title's id in `titles` when it is there (reacted to but never shelved), else null. */
async function titleIdOf(supabase: Awaited<ReturnType<typeof createClient>>, kind: string, tmdbId: number, withTests: boolean) {
  let q = supabase.from("titles").select("id").eq("kind", kind).eq("tmdb_id", tmdbId);
  if (!withTests) q = q.eq("is_test", false);
  const { data } = await q.maybeSingle();
  return (data as { id: number } | null)?.id ?? null;
}
