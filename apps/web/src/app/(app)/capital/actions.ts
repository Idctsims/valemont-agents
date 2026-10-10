"use server";

import { refresh } from "next/cache";
import { headers } from "next/headers";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { KINDS, MODES, centsToDecimal, type Kind, type Mode } from "@/lib/capital/types";
import { createClient } from "@/lib/supabase/server";

// Append one bankroll entry, through the owner's session (db/026's RLS and
// column grants apply: the database sets owner_id and created_at). Entries
// are never edited or deleted; a mistake is corrected with an adjustment.
//
// is_test (db/027) is not writable by this role. A test entry goes through
// add_test_bankroll_entry, and only when the request carries the e2e marker
// and this server verifies it (src/lib/capital/marker.ts). A marker that does
// not verify refuses the write; it never falls back to a real entry.
//
// Returns an ActionResult and never throws an expected failure: a thrown
// message is redacted in production (src/lib/action-result.ts).

const MAX_CENTS = 1e14 - 1; // numeric(14,2)

export async function addEntry(input: {
  mode: Mode;
  kind: Kind;
  /** Signed cents for an adjustment; positive cents otherwise. */
  cents: number;
  note: string | null;
  /** The typed confirmation, required for a live entry. */
  confirm?: string;
}): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    if (!MODES.includes(input.mode)) throw new Refusal("Unknown mode.");
    if (!KINDS.includes(input.kind)) throw new Refusal("Unknown entry kind.");
    // The page asks for LIVE to be typed before a live entry; the server
    // checks it again, so no client path can skip it.
    if (input.mode === "live" && input.confirm !== "LIVE") throw new Refusal("Type LIVE to record real money.");
    const cents = input.cents;
    if (!Number.isInteger(cents) || cents === 0 || Math.abs(cents) > MAX_CENTS) {
      throw new Refusal("Enter an amount.");
    }
    if (input.kind !== "adjustment" && cents < 0) {
      throw new Refusal("A deposit or withdrawal is a positive amount; the kind gives the sign.");
    }
    const note = typeof input.note === "string" ? input.note.trim() || null : null;
    if (note && note.length > 500) throw new Refusal("The note is too long (500 characters at most).");

    const marker = decideMarker((await headers()).get(MARKER_HEADER), process.env);
    if (marker.kind === "refused") throw new Refusal(`Test marker refused: ${marker.reason}.`);

    const supabase = await createClient();
    if (marker.kind === "test") {
      if (input.mode !== "paper") throw new Refusal("Test entries are paper only.");
      const { error } = await supabase.rpc("add_test_bankroll_entry", {
        p_marker: marker.marker,
        p_kind: input.kind,
        p_amount: centsToDecimal(cents),
        p_note: note,
      });
      if (error) throw new Refusal(`Couldn't save the test entry: ${error.message}`);
    } else {
      const { error } = await supabase
        .from("bankroll_entries")
        .insert({ mode: input.mode, kind: input.kind, amount: centsToDecimal(cents), note });
      if (error) throw new Refusal(`Couldn't save the entry: ${error.message}`);
    }
    refresh();
  });
}
