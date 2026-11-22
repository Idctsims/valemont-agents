import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { parseSummary, type GameSummary } from "./summary";

// The game page's ESPN read (owner, Nov 21: the Google-style page, "build
// what you think"): ESPN's public summary for one game, fetched server-side
// when the page opens and on its live poll, parsed down to what the page
// shows, and never stored (no table, no worker, no Supabase egress beyond
// the one-row id read). ~0.5 MB from ESPN per fetch, cached 15 s while the
// game is on and 10 min otherwise. Test rows (espn_id 'e2e-…') never fetch.

const BASE = "https://site.api.espn.com/apis/site/v2/sports";
const TIMEOUT_MS = 6_000;

export type SummaryResult = { summary: GameSummary } | { summary: null; reason: string };

type Ref = { espn_id: string; league: string; kind: string; status: string };

export async function loadSummary(supabase: SupabaseClient, id: number): Promise<SummaryResult> {
  const { data, error } = await supabase
    .from("sports_events")
    .select("espn_id, league, kind, status")
    .eq("id", id)
    .maybeSingle();
  if (error) {
    console.error(`gameday: summary ref unread for ${id}: ${error.message}`);
    return { summary: null, reason: "Couldn't read this game's ESPN id." };
  }
  const ref = data as unknown as Ref | null;
  if (!ref || ref.kind !== "game") return { summary: null, reason: "No ESPN summary for this event." };
  if (ref.espn_id.startsWith("e2e-")) return { summary: null, reason: "A test game has no ESPN summary." };
  const { data: lg, error: lgErr } = await supabase.from("sports_leagues").select("espn_path").eq("code", ref.league).maybeSingle();
  if (lgErr) console.error(`gameday: league path unread for ${ref.league}: ${lgErr.message}`);
  const path = (lg as { espn_path?: string } | null)?.espn_path;
  if (!path || !/^[a-z]+\/[a-z0-9.-]+$/.test(path) || !/^\d+$/.test(ref.espn_id)) {
    return { summary: null, reason: "No ESPN summary for this event." };
  }
  const live = ref.status === "live" || ref.status === "delayed";
  try {
    const res = await fetch(`${BASE}/${path}/summary?event=${ref.espn_id}`, {
      next: { revalidate: live ? 15 : 600 },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const summary = parseSummary(await res.json(), ref.league);
    if (!summary) throw new Error("unexpected shape (no header competitors)");
    return { summary };
  } catch (e) {
    console.error(`gameday: ESPN summary failed for ${id} (${ref.league} ${ref.espn_id}): ${(e as Error).message}`);
    return { summary: null, reason: "ESPN's game data didn't load." };
  }
}
