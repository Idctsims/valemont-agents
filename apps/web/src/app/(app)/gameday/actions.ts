"use server";

import { requireOwner } from "@/lib/auth";
import { Refusal, settle, type ActionResult } from "@/lib/action-result";
import { loadLive } from "@/lib/gameday/data";
import { POLL_MAX, type LivePatch } from "@/lib/gameday/slate";
import { createClient } from "@/lib/supabase/server";

// The live poll (owner, 2026-11-04): the client sends only the ids on screen
// that are in play or due to start; the database returns only what moves in
// play (db/068 gameday_live). Nothing is polled when nothing is live.

export async function pollLive(ids: number[]): Promise<ActionResult<{ rows: LivePatch[] }>> {
  return settle(async () => {
    await requireOwner();
    if (!Array.isArray(ids) || ids.length === 0) return { rows: [] };
    const clean = [...new Set(ids)].filter((n) => Number.isSafeInteger(n) && n > 0).slice(0, POLL_MAX);
    if (clean.length === 0) throw new Refusal("No games to refresh.");
    return { rows: await loadLive(await createClient(), clean) };
  }, "Couldn't refresh the live scores.");
}
