import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { chicagoDay } from "@/lib/screening/types";

import {
  buildQuestions, QUIZ_MIN_SAVED, weekStart, type Distractors, type Question, type QuestionView,
  type QuizItem, type StoredCard,
} from "./quiz";
import type { Category, RefStatus, Room, Verification } from "./types";

// My Arsenal and the Monday quiz (Chat 5 Phase 2; db/059), through the
// owner's session (RLS: her rows). Narrow columns, own rows by id. A test
// row shows only to a request with the verified e2e marker (`withTests`):
// My Arsenal then lists real and test saves; the quiz is a TEST quiz built
// from test saves only, so a run never touches the real week's quiz.

type SavedRow = { save_id: number; ref_id: number | null; word_id: number | null; saved_at: string; is_test: boolean };

/** The saved set among these targets ("ref:12", "word:4"). */
export async function savedKeys(supabase: SupabaseClient, refIds: number[], wordIds: number[]): Promise<Set<string>> {
  const out = new Set<string>();
  const [refs, words] = await Promise.all([
    refIds.length ? supabase.from("v_arsenal_saved").select("ref_id").in("ref_id", refIds) : null,
    wordIds.length ? supabase.from("v_arsenal_saved").select("word_id").in("word_id", wordIds) : null,
  ]);
  for (const [res, kind] of [[refs, "ref"], [words, "word"]] as const) {
    if (!res) continue;
    if (res.error) throw new Error(`Couldn't read your saves: ${res.error.message}`);
    for (const r of (res.data ?? []) as Record<string, number>[]) out.add(`${kind}:${r[`${kind}_id`]}`);
  }
  return out;
}

export type SavedRef = {
  kind: "ref"; id: number; savedAt: string; isTest: boolean;
  category: Category; title: string; origin: string; meaning: string; deployLine: string; room: Room;
  sourceWork: string; quote: string | null; quoteBy: string | null; sourceUrl: string | null;
  verification: Verification; verificationNote: string | null; status: RefStatus; held: boolean;
  heldNote: string | null; fromArmMe: boolean;
};
export type SavedWord = {
  kind: "word"; id: number; savedAt: string; isTest: boolean;
  lemma: string; pronunciation: string | null; meaning: string; room: Room; sentence: string;
  verification: Verification; verificationNote: string | null;
};
export type SavedItem = SavedRef | SavedWord;

const SAVED_SHOWN = 200;

async function savedRows(supabase: SupabaseClient, withTests: boolean, onlyTests = false): Promise<SavedRow[]> {
  let q = supabase.from("v_arsenal_saved").select("save_id, ref_id, word_id, saved_at, is_test");
  if (onlyTests) q = q.eq("is_test", true);
  else if (!withTests) q = q.eq("is_test", false);
  const { data, error } = await q.order("saved_at", { ascending: false }).limit(SAVED_SHOWN);
  if (error) throw new Error(`Couldn't read your saves: ${error.message}`);
  return (data ?? []) as SavedRow[];
}

/** My Arsenal: every saved reference and word, newest first. */
export async function mySaved(supabase: SupabaseClient, opts: { withTests?: boolean } = {}): Promise<SavedItem[]> {
  const rows = await savedRows(supabase, !!opts.withTests);
  const refIds = rows.flatMap((r) => (r.ref_id ? [r.ref_id] : []));
  const wordIds = rows.flatMap((r) => (r.word_id ? [r.word_id] : []));
  const [refs, words, holds] = await Promise.all([
    refIds.length
      ? supabase.from("arsenal_refs").select("id, category, title, origin, meaning, deploy_line, room, source_work, quote, "
          + "quote_by, source_url, verification, verification_note, status, origin_kind").in("id", refIds)
      : null,
    wordIds.length
      ? supabase.from("arsenal_words").select("id, lemma, pronunciation, meaning, room, sentence, verification, "
          + "verification_note").in("id", wordIds)
      : null,
    refIds.length ? supabase.from("v_arsenal_active_holds").select("ref_id, reason, detail").in("ref_id", refIds) : null,
  ]);
  for (const res of [refs, words, holds]) if (res?.error) throw new Error(`Couldn't read My Arsenal: ${res.error.message}`);
  type RefRow = {
    id: number; category: Category; title: string; origin: string; meaning: string; deploy_line: string; room: Room;
    source_work: string; quote: string | null; quote_by: string | null; source_url: string | null;
    verification: Verification; verification_note: string | null; status: RefStatus; origin_kind: string;
  };
  type WordRow = {
    id: number; lemma: string; pronunciation: string | null; meaning: string; room: Room; sentence: string;
    verification: Verification; verification_note: string | null;
  };
  const refById = new Map(((refs?.data ?? []) as unknown as RefRow[]).map((r) => [r.id, r]));
  const wordById = new Map(((words?.data ?? []) as unknown as WordRow[]).map((w) => [w.id, w]));
  const holdById = new Map(((holds?.data ?? []) as { ref_id: number; reason: string; detail: string | null }[])
    .map((h) => [h.ref_id, h]));
  return rows.flatMap((s): SavedItem[] => {
    if (s.ref_id) {
      const r = refById.get(s.ref_id);
      if (!r) return [];
      const hold = holdById.get(r.id);
      return [{
        kind: "ref", id: r.id, savedAt: s.saved_at, isTest: s.is_test, category: r.category, title: r.title,
        origin: r.origin, meaning: r.meaning, deployLine: r.deploy_line, room: r.room, sourceWork: r.source_work,
        quote: r.quote, quoteBy: r.quote_by, sourceUrl: r.source_url, verification: r.verification,
        verificationNote: r.verification_note, status: r.status, held: !!hold,
        heldNote: hold ? `${hold.reason}${hold.detail ? `: ${hold.detail}` : ""}` : null, fromArmMe: r.origin_kind === "arm_me",
      }];
    }
    const w = s.word_id ? wordById.get(s.word_id) : undefined;
    if (!w) return [];
    return [{
      kind: "word", id: w.id, savedAt: s.saved_at, isTest: s.is_test, lemma: w.lemma, pronunciation: w.pronunciation,
      meaning: w.meaning, room: w.room, sentence: w.sentence, verification: w.verification,
      verificationNote: w.verification_note,
    }];
  });
}

// ---------------------------------------------------------------- the quiz

export type QuizAnswer = { chosen: number; correct: boolean; answer: number };
export type WeekQuiz =
  | { state: "locked"; saved: number; weekStart: string }
  | { state: "empty"; saved: number; weekStart: string }
  | { state: "ready"; id: number; weekStart: string; isTest: boolean; questions: QuestionView[];
      answers: Record<number, QuizAnswer> };

/** The saved items as quiz items, each with its latest review (FSRS due). */
async function quizItems(supabase: SupabaseClient, testWorld: boolean): Promise<QuizItem[]> {
  const rows = await savedRows(supabase, testWorld, testWorld);
  const refIds = rows.flatMap((r) => (r.ref_id ? [r.ref_id] : []));
  const wordIds = rows.flatMap((r) => (r.word_id ? [r.word_id] : []));
  const [refs, words, reviews] = await Promise.all([
    refIds.length ? supabase.from("arsenal_refs").select("id, title, meaning").in("id", refIds) : null,
    wordIds.length ? supabase.from("arsenal_words").select("id, lemma, sentence").in("id", wordIds) : null,
    rows.length
      ? supabase.from("arsenal_quiz_reviews").select("ref_id, word_id, due_at, created_at")
        .or([refIds.length ? `ref_id.in.(${refIds.join(",")})` : "", wordIds.length ? `word_id.in.(${wordIds.join(",")})` : ""]
          .filter(Boolean).join(","))
        .order("created_at", { ascending: false }).order("id", { ascending: false }).limit(2000)
      : null,
  ]);
  for (const res of [refs, words, reviews]) if (res?.error) throw new Error(`Couldn't read the quiz items: ${res.error.message}`);
  const history = new Map<string, { due: string; last: string; n: number }>();
  for (const v of (reviews?.data ?? []) as { ref_id: number | null; word_id: number | null; due_at: string; created_at: string }[]) {
    const k = v.ref_id ? `ref:${v.ref_id}` : `word:${v.word_id}`;
    const h = history.get(k);
    if (h) h.n += 1;
    else history.set(k, { due: v.due_at, last: v.created_at, n: 1 });
  }
  const refById = new Map(((refs?.data ?? []) as { id: number; title: string; meaning: string }[]).map((r) => [r.id, r]));
  const wordById = new Map(((words?.data ?? []) as { id: number; lemma: string; sentence: string }[]).map((w) => [w.id, w]));
  return rows.flatMap((s): QuizItem[] => {
    const k = s.ref_id ? `ref:${s.ref_id}` : `word:${s.word_id}`;
    const h = history.get(k);
    const base = { savedAt: s.saved_at, due: h?.due ?? null, lastReview: h?.last ?? null, reviews: h?.n ?? 0 };
    if (s.ref_id) {
      const r = refById.get(s.ref_id);
      return r ? [{ kind: "ref", id: r.id, title: r.title, meaning: r.meaning, ...base }] : [];
    }
    const w = s.word_id ? wordById.get(s.word_id) : undefined;
    return w ? [{ kind: "word", id: w.id, lemma: w.lemma, sentence: w.sentence, ...base }] : [];
  });
}

/** Dropped references and words of the same world: distractors after the saved ones. */
async function distractors(supabase: SupabaseClient, testWorld: boolean): Promise<Distractors> {
  const [refs, words] = await Promise.all([
    supabase.from("arsenal_refs").select("id, title, meaning").eq("status", "dropped").eq("is_test", testWorld)
      .order("id", { ascending: false }).limit(60),
    supabase.from("arsenal_words").select("id, lemma, sentence").eq("status", "dropped").eq("is_test", testWorld)
      .order("id", { ascending: false }).limit(60),
  ]);
  if (refs.error || words.error) throw new Error(`Couldn't read the quiz's distractors: ${(refs.error ?? words.error)!.message}`);
  return {
    refs: (refs.data ?? []) as Distractors["refs"],
    words: (words.data ?? []) as Distractors["words"],
  };
}

async function findQuiz(supabase: SupabaseClient, week: string, testWorld: boolean) {
  const { data, error } = await supabase.from("arsenal_quizzes").select("id, week_start, questions, is_test")
    .eq("week_start", week).eq("is_test", testWorld).order("id", { ascending: false }).limit(1);
  if (error) throw new Error(`Couldn't read this week's quiz: ${error.message}`);
  return ((data ?? []) as { id: number; week_start: string; questions: Question[]; is_test: boolean }[])[0] ?? null;
}

/**
 * This week's quiz, built ONCE on first open (an append-only row; one real
 * quiz per week by unique index), then resumed. Locked under 4 saved items.
 * With the marker it is a test quiz of test saves; `fresh` builds a new test
 * quiz (test quizzes are not unique), so a spec starts from question 1.
 */
export async function ensureWeekQuiz(supabase: SupabaseClient,
                                     opts: { withTests?: boolean; freshTest?: boolean } = {}): Promise<WeekQuiz> {
  const testWorld = !!opts.withTests;
  const week = weekStart(chicagoDay());
  let quiz = opts.freshTest && testWorld ? null : await findQuiz(supabase, week, testWorld);
  if (!quiz) {
    const items = await quizItems(supabase, testWorld);
    if (items.length < QUIZ_MIN_SAVED) return { state: "locked", saved: items.length, weekStart: week };
    const questions = buildQuestions(items, await distractors(supabase, testWorld), new Date());
    if (!questions.length) return { state: "empty", saved: items.length, weekStart: week };
    const { error } = await supabase.from("arsenal_quizzes").insert({ week_start: week, questions });
    // Another tab built it first (the unique index): read that one.
    if (error && error.code !== "23505") throw new Error(`Couldn't build this week's quiz: ${error.message}`);
    quiz = await findQuiz(supabase, week, testWorld);
    if (!quiz) throw new Error("This week's quiz was built but could not be read back.");
  }
  const { data, error } = await supabase.from("arsenal_quiz_reviews").select("question_no, chosen, correct")
    .eq("quiz_id", quiz.id);
  if (error) throw new Error(`Couldn't read your answers: ${error.message}`);
  const answers: Record<number, QuizAnswer> = {};
  for (const r of (data ?? []) as { question_no: number; chosen: number; correct: boolean }[]) {
    answers[r.question_no] = { chosen: r.chosen, correct: r.correct, answer: quiz.questions[r.question_no - 1].answer };
  }
  return {
    state: "ready", id: quiz.id, weekStart: quiz.week_start, isTest: quiz.is_test, answers,
    // The answer index never reaches the browser before the answer.
    questions: quiz.questions.map((q): QuestionView => ({
      no: q.no, kind: q.kind, prompt: q.prompt, options: q.options,
      ...(q.ref_id != null ? { ref_id: q.ref_id } : { word_id: q.word_id }),
    })),
  };
}

/** The item's latest FSRS card (null: never reviewed). */
export async function latestCard(supabase: SupabaseClient, target: { ref_id?: number; word_id?: number }): Promise<StoredCard | null> {
  const [col, id] = target.ref_id ? ["ref_id", target.ref_id] : ["word_id", target.word_id!];
  const { data, error } = await supabase.from("arsenal_quiz_reviews").select("fsrs_state").eq(col, id)
    .order("created_at", { ascending: false }).order("id", { ascending: false }).limit(1);
  if (error) throw new Error(`Couldn't read the last review: ${error.message}`);
  return ((data ?? []) as { fsrs_state: StoredCard }[])[0]?.fsrs_state ?? null;
}

/** One stored quiz, answers included (the answer action's read). */
export async function quizById(supabase: SupabaseClient, id: number) {
  const { data, error } = await supabase.from("arsenal_quizzes").select("id, questions").eq("id", id).maybeSingle();
  if (error) throw new Error(`Couldn't read the quiz: ${error.message}`);
  return data as { id: number; questions: Question[] } | null;
}
