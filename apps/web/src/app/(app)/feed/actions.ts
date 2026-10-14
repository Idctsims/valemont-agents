"use server";

import { refresh } from "next/cache";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { CARD_REACTIONS, type CardReaction, type Pillar } from "@/lib/feed/types";
import { createClient } from "@/lib/supabase/server";

// A card's reaction (db/039 feed_reactions): love, neutral, not_interested,
// or undo (takes the latest one back; a loved card's second Love tap, and the
// Undo toast). Rows are append-only: the database refuses UPDATE and DELETE,
// and the latest non-open row per story and pillar is what counts
// (v_feed_reaction_latest). Written through the owner's session, so RLS
// applies (owner_id defaults to auth.uid(); the policy checks it). Returns an
// ActionResult and never throws an expected failure (src/lib/action-result.ts).

// Tech (db/045): a tech reaction only on a tech story; the database's
// feed_pillar_guard refuses a cross-line row, and the error reads plainly.
const PILLARS: Pillar[] = ["home", "sports", "tech"];

export async function reactToStory(input: { storyId: number; pillar: Pillar; reaction: CardReaction }): Promise<ActionResult> {
  return settle(async () => {
    await requireOwner();
    const storyId = Number(input.storyId);
    if (!Number.isSafeInteger(storyId) || storyId < 1) throw new Refusal("Unknown story.");
    if (!PILLARS.includes(input.pillar)) throw new Refusal("Unknown feed.");
    if (!CARD_REACTIONS.includes(input.reaction)) throw new Refusal("Unknown reaction.");
    const supabase = await createClient();
    const { error } = await supabase
      .from("feed_reactions")
      .insert({ story_id: storyId, pillar: input.pillar, reaction: input.reaction });
    if (error) {
      if (error.code === "23503") throw new Refusal("That story is gone.");
      if (error.message.includes("crosses the tech line")) throw new Refusal("That story belongs to another feed.");
      throw new Refusal(`Couldn't save that: ${error.message}`);
    }
    refresh();
  });
}
