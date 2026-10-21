import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { chicagoDay } from "@/lib/screening/types";

import { savedKeys } from "./saved";
import type { Drop, PastDrop, Ref, RefReaction, UnverifiedRef, Word } from "./types";

// The Arsenal's reads (Chat 5 Phase 1; db/054), through the owner's session
// (RLS: she reads everything here). Narrow columns, own rows by id. A test
// row shows only to a request with the verified e2e marker (`withTests`):
// then today's newest TEST drop is shown in place of the real one.

const PAST_DAYS = 30;
const PAST_SHOWN = 14;
const UNVERIFIED_SHOWN = 20;

const REF_COLS = "id, category, title, origin, meaning, deploy_line, room, source_work, quote, quote_by, source_url, "
  + "verification, verification_note, is_test";
const WORD_COLS = "id, lemma, pronunciation, etymology, etymology_url, meaning, room, sentence, verification, is_test";

type RefRow = {
  id: number; category: Ref["category"]; title: string; origin: string; meaning: string; deploy_line: string;
  room: Ref["room"]; source_work: string; quote: string | null; quote_by: string | null; source_url: string | null;
  verification: Ref["verification"]; verification_note: string | null; is_test: boolean;
};
type WordRow = {
  id: number; lemma: string; pronunciation: string | null; etymology: string | null; etymology_url: string | null;
  meaning: string; room: Word["room"]; sentence: string; verification: Word["verification"]; is_test: boolean;
};
type DropRow = { id: number; drop_date: string; ref_ids: number[]; word_id: number; is_test: boolean };

/** The latest reaction per target, among these ids ('none' clears). */
async function latestReactions(supabase: SupabaseClient, column: "ref_id" | "word_id", ids: number[]) {
  const out = new Map<number, RefReaction | null>();
  if (!ids.length) return out;
  const { data, error } = await supabase.from("arsenal_reactions").select(`${column}, reaction`)
    .in(column, ids).order("created_at", { ascending: false }).order("id", { ascending: false });
  if (error) throw new Error(`Couldn't read your reactions: ${error.message}`);
  for (const r of (data ?? []) as Record<string, unknown>[]) {
    const id = r[column] as number;
    if (!out.has(id)) out.set(id, r.reaction === "none" ? null : (r.reaction as RefReaction));
  }
  return out;
}

function toRef(r: RefRow, reaction: RefReaction | null, saved: boolean): Ref {
  return {
    id: r.id, category: r.category, title: r.title, origin: r.origin, meaning: r.meaning, deployLine: r.deploy_line,
    room: r.room, sourceWork: r.source_work, quote: r.quote, quoteBy: r.quote_by, sourceUrl: r.source_url,
    verification: r.verification, verificationNote: r.verification_note, isTest: r.is_test, reaction, saved,
  };
}

/** Today's drop (the owner's date), refs in slot order. */
export async function todayDrop(supabase: SupabaseClient, opts: { withTests?: boolean } = {}): Promise<Drop | null> {
  const today = chicagoDay();
  let q = supabase.from("arsenal_drops").select("id, drop_date, ref_ids, word_id, is_test").eq("drop_date", today);
  if (!opts.withTests) q = q.eq("is_test", false);
  // With the marker, the newest test drop wins over the real one.
  const { data, error } = await q.order("is_test", { ascending: false }).order("id", { ascending: false }).limit(1);
  if (error) throw new Error(`Couldn't read today's drop: ${error.message}`);
  const d = ((data ?? []) as DropRow[])[0];
  if (!d) return null;
  const [refs, word, refReactions, wordReactions, saved] = await Promise.all([
    supabase.from("arsenal_refs").select(REF_COLS).in("id", d.ref_ids),
    supabase.from("arsenal_words").select(WORD_COLS).eq("id", d.word_id).single(),
    latestReactions(supabase, "ref_id", d.ref_ids),
    latestReactions(supabase, "word_id", [d.word_id]),
    savedKeys(supabase, d.ref_ids, [d.word_id]),
  ]);
  if (refs.error) throw new Error(`Couldn't read today's references: ${refs.error.message}`);
  if (word.error) throw new Error(`Couldn't read today's Power Word: ${word.error.message}`);
  const byId = new Map(((refs.data ?? []) as unknown as RefRow[]).map((r) => [r.id, r]));
  const w = word.data as unknown as WordRow;
  return {
    id: d.id, date: d.drop_date, isTest: d.is_test,
    refs: d.ref_ids.map((id) => byId.get(id)).filter((r): r is RefRow => !!r)
      .map((r) => toRef(r, refReactions.get(r.id) ?? null, saved.has(`ref:${r.id}`))),
    word: {
      id: w.id, lemma: w.lemma, pronunciation: w.pronunciation, etymology: w.etymology, etymologyUrl: w.etymology_url,
      meaning: w.meaning, room: w.room, sentence: w.sentence, verification: w.verification, isTest: w.is_test,
      reaction: wordReactions.get(w.id) ?? null, saved: saved.has(`word:${w.id}`),
    },
  };
}

/** The drops before today (the last 30 days, newest first, at most 14). */
export async function pastDrops(supabase: SupabaseClient, opts: { withTests?: boolean } = {}): Promise<PastDrop[]> {
  let q = supabase.from("arsenal_drops").select("id, drop_date, ref_ids, word_id, is_test")
    .lt("drop_date", chicagoDay()).gte("drop_date", chicagoDay(-PAST_DAYS));
  if (!opts.withTests) q = q.eq("is_test", false);
  const { data, error } = await q.order("drop_date", { ascending: false }).order("id", { ascending: false }).limit(PAST_SHOWN);
  if (error) throw new Error(`Couldn't read past drops: ${error.message}`);
  const drops = (data ?? []) as DropRow[];
  if (!drops.length) return [];
  const refIds = [...new Set(drops.flatMap((d) => d.ref_ids))];
  const [refs, words] = await Promise.all([
    supabase.from("arsenal_refs").select("id, title, category, room, source_work, deploy_line").in("id", refIds),
    supabase.from("arsenal_words").select("id, lemma").in("id", drops.map((d) => d.word_id)),
  ]);
  if (refs.error) throw new Error(`Couldn't read past references: ${refs.error.message}`);
  if (words.error) throw new Error(`Couldn't read past Power Words: ${words.error.message}`);
  type Row = { id: number; title: string; category: Ref["category"]; room: Ref["room"]; source_work: string; deploy_line: string };
  const refById = new Map(((refs.data ?? []) as Row[]).map((r) => [r.id, r]));
  const wordById = new Map(((words.data ?? []) as { id: number; lemma: string }[]).map((w) => [w.id, w.lemma]));
  return drops.map((d) => ({
    id: d.id, date: d.drop_date, isTest: d.is_test, word: wordById.get(d.word_id) ?? "",
    refs: d.ref_ids.map((id) => refById.get(id)).filter((r): r is Row => !!r)
      .map((r) => ({ id: r.id, title: r.title, category: r.category, room: r.room, sourceWork: r.source_work, deployLine: r.deploy_line })),
  }));
}

/** References the verifier could not stand behind (never dropped), newest first. */
export async function unverifiedRefs(supabase: SupabaseClient, opts: { withTests?: boolean } = {}): Promise<UnverifiedRef[]> {
  let q = supabase.from("arsenal_refs").select("id, title, category, source_work, verification_note, is_test")
    .eq("verification", "unverified").eq("status", "pool");
  if (!opts.withTests) q = q.eq("is_test", false);
  const { data, error } = await q.order("is_test", { ascending: false }).order("id", { ascending: false }).limit(UNVERIFIED_SHOWN);
  if (error) throw new Error(`Couldn't read the unverified references: ${error.message}`);
  return ((data ?? []) as { id: number; title: string; category: Ref["category"]; source_work: string;
    verification_note: string | null; is_test: boolean }[])
    .map((r) => ({ id: r.id, title: r.title, category: r.category, sourceWork: r.source_work, note: r.verification_note, isTest: r.is_test }));
}
