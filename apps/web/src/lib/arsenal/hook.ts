import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import type { HookCard, TitleHook } from "./hook-view";
import { savedKeys } from "./saved";
import type { Ref, RefStatus, Room, Verification } from "./types";

// The Screening Room hook (Chat 5 Phase 3; db/062), through the owner's
// session (RLS: she reads hooks, cards and titles). Narrow columns, own rows
// by id. A finished title's hook is ONE row, ever: queued while the worker
// drafts, drafted with its cards and word, or failed with the reason.

/** A title's hook and its cards (in the order drafted), or null when it has none. */
export async function titleHook(supabase: SupabaseClient, titleId: number): Promise<TitleHook | null> {
  const { data, error } = await supabase.from("arsenal_title_hooks").select("status, note, ref_ids, word_id")
    .eq("title_id", titleId).maybeSingle();
  if (error) throw new Error(`Couldn't read this title's Arsenal cards: ${error.message}`);
  const h = data as { status: TitleHook["status"]; note: string | null; ref_ids: number[]; word_id: number | null } | null;
  if (!h) return null;
  if (h.status !== "drafted") return { status: h.status, note: h.note, cards: [], word: null };
  const [refs, word, holds, saved] = await Promise.all([
    supabase.from("arsenal_refs").select("id, category, title, origin, meaning, deploy_line, room, source_work, quote, "
      + "quote_by, source_url, verification, verification_note, status, is_test").in("id", h.ref_ids),
    h.word_id
      ? supabase.from("arsenal_words").select("id, lemma, pronunciation, meaning, room, sentence, verification, "
          + "verification_note, is_test").eq("id", h.word_id).maybeSingle()
      : null,
    supabase.from("v_arsenal_active_holds").select("ref_id, reason, detail").in("ref_id", h.ref_ids),
    savedKeys(supabase, h.ref_ids, h.word_id ? [h.word_id] : []),
  ]);
  for (const res of [refs, word, holds]) if (res?.error) throw new Error(`Couldn't read this title's Arsenal cards: ${res.error.message}`);
  type Row = {
    id: number; category: Ref["category"]; title: string; origin: string; meaning: string; deploy_line: string; room: Room;
    source_work: string; quote: string | null; quote_by: string | null; source_url: string | null;
    verification: Verification; verification_note: string | null; status: RefStatus; is_test: boolean;
  };
  const byId = new Map(((refs.data ?? []) as unknown as Row[]).map((r) => [r.id, r]));
  const holdById = new Map(((holds.data ?? []) as { ref_id: number; reason: string; detail: string | null }[])
    .map((x) => [x.ref_id, x]));
  const cards = h.ref_ids.map((id) => byId.get(id)).filter((r): r is Row => !!r).map((r): HookCard => {
    const hold = holdById.get(r.id);
    return {
      id: r.id, category: r.category, title: r.title, origin: r.origin, meaning: r.meaning, deployLine: r.deploy_line,
      room: r.room, sourceWork: r.source_work, quote: r.quote, quoteBy: r.quote_by, sourceUrl: r.source_url,
      verification: r.verification, verificationNote: r.verification_note, isTest: r.is_test, reaction: null,
      saved: saved.has(`ref:${r.id}`), fromTitle: null, status: r.status, held: !!hold,
      heldNote: hold ? `${hold.reason}${hold.detail ? `: ${hold.detail}` : ""}` : null,
    };
  });
  const w = word?.data as {
    id: number; lemma: string; pronunciation: string | null; meaning: string; room: Room; sentence: string;
    verification: Verification; verification_note: string | null; is_test: boolean;
  } | null | undefined;
  return {
    status: "drafted", note: h.note, cards,
    word: w ? {
      id: w.id, lemma: w.lemma, pronunciation: w.pronunciation, meaning: w.meaning, room: w.room, sentence: w.sentence,
      verification: w.verification, verificationNote: w.verification_note, saved: saved.has(`word:${w.id}`), isTest: w.is_test,
    } : null,
  };
}
