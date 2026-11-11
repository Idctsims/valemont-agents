"use server";

import { headers } from "next/headers";

import { requireOwner } from "@/lib/auth";
import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { isKind, type BoardEvent } from "@/lib/betting/board";
import { cleanReason, requestLegs, type ComboState } from "@/lib/betting/combo";
import { createComboRequest, loadComboRequest, loadGame } from "@/lib/betting/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

// A game's markets, loaded when the owner expands it (owner, Nov 6: props load
// on expand, never with the page); in Combo mode with each market's ticker
// (db/077). And combo pricing (db/076): ask for a price, then read it back.
// Nothing here bets (CLAUDE.md §1).

const TICKER = /^[A-Za-z0-9][A-Za-z0-9._-]{1,100}$/;

export async function expandGame(
  game: string,
  kind: string | null,
  combo: boolean,
  tickers = false,
): Promise<ActionResult<{ events: BoardEvent[] }>> {
  return settle(async () => {
    await requireOwner();
    if (typeof game !== "string" || !TICKER.test(game)) throw new Refusal("That game isn't on the board.");
    const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
    const events = await loadGame(await createClient(), {
      game, kind: isKind(kind) ? kind : null, combo: combo === true, withTests, tickers: tickers === true,
    });
    return { events };
  }, "Couldn't load that game's markets.");
}

/** Ask the worker for a combo price. The id to poll, or the refusal's words. */
export async function priceCombo(legs: unknown[]): Promise<ActionResult<{ id: number }>> {
  return settle(async () => {
    await requireOwner();
    const req = requestLegs(legs);
    if (!req) throw new Refusal("Pick 2 to 25 legs.");
    const res = await createComboRequest(await createClient(), req);
    if ("refused" in res) throw new Refusal(cleanReason(res.refused));
    return { id: res.id };
  }, "Couldn't ask for a price.");
}

/** A request's state: queued, answered with its quote, or refused/failed with why. */
export async function comboStatus(id: number): Promise<ActionResult<{ state: ComboState }>> {
  return settle(async () => {
    await requireOwner();
    if (!Number.isSafeInteger(id) || id <= 0) throw new Refusal("No such price.");
    const state = await loadComboRequest(await createClient(), id);
    if (!state) throw new Refusal("That price is gone (prices are kept 7 days).");
    return { state };
  }, "Couldn't read the price.");
}
