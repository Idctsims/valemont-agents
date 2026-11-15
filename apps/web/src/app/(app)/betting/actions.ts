"use server";

import { headers } from "next/headers";

import { requireOwner } from "@/lib/auth";
import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { cleanReason, requestLegs, type ComboState } from "@/lib/betting/combo";
import { createComboRequest, loadComboRequest, loadGameTab } from "@/lib/betting/data";
import { isTab, type TabEvent } from "@/lib/betting/game";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

// A game page's tabs, one at a time (db/079), and combo pricing (db/076): ask
// for a price, then read it back. Nothing here bets (CLAUDE.md §1).

const TICKER = /^[A-Za-z0-9][A-Za-z0-9._-]{1,100}$/;

/** One tab of a game's page (db/079), loaded once and cached by the page; the Players tab one stat
 *  at a time (never the whole tab: 108-150 KB on an NBA game). */
export async function loadTab(game: string, tab: string, stat: string | null): Promise<ActionResult<{ events: TabEvent[] }>> {
  return settle(async () => {
    await requireOwner();
    if (typeof game !== "string" || !TICKER.test(game)) throw new Refusal("That game isn't on the board.");
    if (!isTab(tab)) throw new Refusal("No such tab.");
    if (tab === "players" && (typeof stat !== "string" || stat.length > 120)) throw new Refusal("Pick a stat.");
    const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
    const events = await loadGameTab(await createClient(), game, tab, tab === "players" ? stat : null, withTests);
    return { events };
  }, "Couldn't load those markets.");
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
