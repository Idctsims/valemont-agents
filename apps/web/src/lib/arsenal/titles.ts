import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { titleHref, type FromTitle } from "./types";

/** "From <title>" for each of these title ids (db/062 source_title_id): the
 *  finished title a card was drafted from, linking to its page. */
export async function fromTitles(supabase: SupabaseClient, titleIds: (number | null)[]): Promise<Map<number, FromTitle>> {
  const out = new Map<number, FromTitle>();
  const ids = [...new Set(titleIds.filter((i): i is number => i != null))];
  if (!ids.length) return out;
  const { data, error } = await supabase.from("titles").select("id, name, kind, tmdb_id").in("id", ids);
  if (error) throw new Error(`Couldn't read the cards' titles: ${error.message}`);
  for (const t of (data ?? []) as { id: number; name: string; kind: "movie" | "tv"; tmdb_id: number }[]) {
    out.set(t.id, { name: t.name, href: titleHref(t.kind, t.tmdb_id) });
  }
  return out;
}
