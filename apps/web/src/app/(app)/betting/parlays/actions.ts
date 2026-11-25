"use server";

import { headers } from "next/headers";

import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { requireOwner } from "@/lib/auth";
import { loadParlayDetails, type ParlayDetails } from "@/lib/betting/parlay-data";
import { LIVE_GAMES_MAX, loadParlayLive, type LiveRequest } from "@/lib/betting/parlay-live";
import type { GameLive } from "@/lib/betting/parlays";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

// The Parlays page's two server actions (Phase 4.5): the live count (every
// 30 s while a ticket's game is on, and once on open for the photos), and one
// ticket's Details on open. Read-only: nothing here bets (CLAUDE.md §1).

export async function pollParlays(req: LiveRequest[]): Promise<ActionResult<{ games: Record<string, GameLive> }>> {
  return settle(async () => {
    await requireOwner();
    if (!Array.isArray(req)) throw new Refusal("No games to refresh.");
    if (req.length === 0) return { games: {} };
    return { games: await loadParlayLive(await createClient(), req.slice(0, LIVE_GAMES_MAX)) };
  }, "Couldn't refresh the live counts.");
}

export async function parlayDetails(id: number): Promise<ActionResult<{ ticket: ParlayDetails }>> {
  return settle(async () => {
    await requireOwner();
    if (!Number.isSafeInteger(id) || id <= 0) throw new Refusal("No ticket to open.");
    const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
    const ticket = await loadParlayDetails(await createClient(), id, withTests);
    if (!ticket) throw new Refusal("That ticket isn't there.");
    return { ticket };
  }, "Couldn't open the ticket.");
}
