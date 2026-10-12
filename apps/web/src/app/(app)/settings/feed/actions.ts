"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

// Confirm or Dismiss a move the summarizer proposed (db/042). Through the
// owner's session: decide_entity_link_proposal() is SECURITY INVOKER, so RLS
// applies, and in one transaction it writes the decision (once; the database
// refuses a second) and, on Confirm, ends the old link and adds the new one.
// Returns an ActionResult and never throws an expected failure.

export async function decideMove(input: { id: number; decision: "confirmed" | "dismissed" }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    const id = Number(input.id);
    if (!Number.isSafeInteger(id) || id < 1) throw new Refusal("Unknown proposal.");
    if (input.decision !== "confirmed" && input.decision !== "dismissed") throw new Refusal("Unknown decision.");
    const supabase = await createClient();
    const { error } = await supabase.rpc("decide_entity_link_proposal", { p_id: id, p_decision: input.decision });
    if (error) {
      if (/already (confirmed|dismissed)/.test(error.message)) throw new Refusal("That one was already decided.");
      if (/stale/.test(error.message)) throw new Refusal("The current team changed since this was proposed. Dismiss it.");
      throw new Refusal(`Couldn't save that: ${error.message}`);
    }
    refresh();
  });
}
