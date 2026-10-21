"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { nextCard, ratingOf } from "@/lib/arsenal/quiz";
import { latestCard, quizById } from "@/lib/arsenal/saved";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

// The Arsenal's writes, through the owner's session (RLS: her own rows).
// Each returns an ActionResult and never throws an expected failure.
//   * Reactions (db/054 arsenal_reactions, append-only): Love, Not
//     interested, Already know it, or 'none' to take one back. The database
//     stamps is_test from the target, so a test reaction never trains the
//     real weights.
//   * Saves (db/059 arsenal_saves, append-only): save or unsave a reference
//     or a word. is_test from the target, too. Saving a pending Arm Me card
//     queues its verification (the database's trigger, once ever).
//   * Quiz answers (db/059 arsenal_quiz_reviews, append-only): one row per
//     question, with ts-fsrs's next card; the database recomputes `correct`
//     from the stored answer and refuses a row that disagrees.

const REACTIONS = new Set(["love", "not_interested", "known", "none"]);

function checkId(raw: number, what: string): number {
  const id = Number(raw);
  if (!Number.isSafeInteger(id) || id < 1) throw new Refusal(`Unknown ${what}.`);
  return id;
}

async function insert(row: { ref_id?: number; word_id?: number; reaction: string }) {
  if (!REACTIONS.has(row.reaction)) throw new Refusal("Unknown reaction.");
  const supabase = await createClient();
  const { error } = await supabase.from("arsenal_reactions").insert(row);
  if (error) {
    if (error.code === "23503") throw new Refusal("That card is gone.");
    throw new Refusal(`Couldn't save it: ${error.message}`);
  }
}

/** Love / Not interested / Already know it (or 'none') on a reference. */
export async function reactToRef(input: { refId: number; reaction: string }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    await insert({ ref_id: checkId(input.refId, "reference"), reaction: input.reaction });
    refresh();
  });
}

/** The same on today's Power Word. */
export async function reactToWord(input: { wordId: number; reaction: string }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    await insert({ word_id: checkId(input.wordId, "word"), reaction: input.reaction });
    refresh();
  });
}

/** Save (or unsave) a reference or a word to My Arsenal. */
export async function setSaved(input: { refId?: number; wordId?: number; saved: boolean }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    const target = input.refId != null
      ? { ref_id: checkId(input.refId, "reference") }
      : { word_id: checkId(Number(input.wordId), "word") };
    const supabase = await createClient();
    const { error } = await supabase.from("arsenal_saves").insert({ ...target, action: input.saved ? "save" : "unsave" });
    if (error) {
      // A double tap: the state is already what was asked for.
      if (/already saved|not saved/.test(error.message)) return;
      if (error.code === "23503") throw new Refusal("That card is gone.");
      throw new Refusal(`Couldn't ${input.saved ? "save" : "unsave"} it: ${error.message}`);
    }
    refresh();
  });
}

/** Answer one quiz question: the review row, with the next FSRS card. */
export async function answerQuestion(input: { quizId: number; no: number; chosen: number }): Promise<
  ActionResult<{ correct: boolean; answer: number }>
> {
  return settle(async () => {
    await requireOwner();
    const quizId = checkId(input.quizId, "quiz");
    const chosen = Number(input.chosen);
    if (!Number.isInteger(chosen) || chosen < 0 || chosen > 3) throw new Refusal("Pick one of the four.");
    const supabase = await createClient();
    const quiz = await quizById(supabase, quizId);
    const q = quiz?.questions[Number(input.no) - 1];
    if (!quiz || !q) throw new Refusal("That question isn't in this week's quiz.");
    const correct = chosen === q.answer;
    const target = q.ref_id ? { ref_id: q.ref_id } : { word_id: q.word_id! };
    const now = new Date();
    const card = nextCard(await latestCard(supabase, target), correct, now);
    const { error } = await supabase.from("arsenal_quiz_reviews").insert({
      // week_start, is_test and the owner come from the quiz (db/059's guard).
      quiz_id: quizId, question_no: q.no, question_kind: q.kind, ...target,
      chosen, correct, rating: ratingOf(correct), fsrs_state: card, due_at: card.due,
    });
    if (error) {
      if (error.code === "23505") throw new Refusal("You already answered that one.");
      throw new Refusal(`Couldn't record the answer: ${error.message}`);
    }
    return { correct, answer: q.answer };
  });
}
