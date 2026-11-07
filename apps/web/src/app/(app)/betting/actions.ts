"use server";

import { headers } from "next/headers";

import { requireOwner } from "@/lib/auth";
import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { isKind, type BoardEvent } from "@/lib/betting/board";
import { loadGame } from "@/lib/betting/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

// A game's markets, loaded when the owner expands it (owner, Nov 6: props load
// on expand, never with the page). Read-only; nothing here writes.

const TICKER = /^[A-Za-z0-9][A-Za-z0-9._-]{1,100}$/;

export async function expandGame(
  game: string,
  kind: string | null,
  combo: boolean,
): Promise<ActionResult<{ events: BoardEvent[] }>> {
  return settle(async () => {
    await requireOwner();
    if (typeof game !== "string" || !TICKER.test(game)) throw new Refusal("That game isn't on the board.");
    const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
    const events = await loadGame(await createClient(), {
      game, kind: isKind(kind) ? kind : null, combo: combo === true, withTests,
    });
    return { events };
  }, "Couldn't load that game's markets.");
}
