"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

// Reactions to an Arsenal reference or Power Word (db/054
// arsenal_reactions, append-only): Love, Not interested, Already know it,
// or 'none' to take one back. Each tap is a NEW row through the owner's
// session (RLS: her own rows only); the latest counts. The database stamps
// is_test from the target, so a test reaction never trains the real
// weights. Returns an ActionResult and never throws an expected failure.

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
