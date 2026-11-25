import "server-only";

import { liveFromKalshi, type Details } from "./kalshi-live-parse";
import type { GameLive } from "./parlays";

export { GAME_TICKER } from "./kalshi-live-parse";

// Kalshi's own live data for a parlay leg's game when Game Day has no ESPN link
// for it (owner, Nov 25: most soccer). Read only and keyless:
//   1. GET /milestones?related_event_ticker=<game ticker>: the game's milestone,
//      once a day per game (Next's data cache).
//   2. GET /live_data/batch?milestone_ids=...: every such game in one call, 15 s
//      in the data cache while the page polls (every 30 s). Nothing is stored.
// Soccer only (milestone type soccer_*), probed on Nov 25 across three feeds
// (Genius, Opta, one unnamed): the score, the clock or "Halftime", the score by
// half where the feed gives it, and the goal scorers. Kalshi titles soccer games
// "home vs away" (PSG vs Roma: PSG's scorer was "home"). Nothing here bets.

const BASE = "https://api.elections.kalshi.com/trade-api/v2";
const TIMEOUT_MS = 6_000;
const HEADERS = { Accept: "application/json", "User-Agent": "valemont/1.0" };

type Milestone = { id: string; type: string; title: string };

async function getJson(path: string, revalidate: number): Promise<unknown> {
  const res = await fetch(`${BASE}${path}`, { headers: HEADERS, next: { revalidate }, signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** The game's milestone, or null when Kalshi has none (or it is not soccer). */
async function milestoneFor(game: string): Promise<Milestone | null> {
  const body = (await getJson(`/milestones?limit=5&related_event_ticker=${encodeURIComponent(game)}`, 86_400)) as {
    milestones?: { id?: unknown; type?: unknown; title?: unknown; primary_event_tickers?: unknown }[];
  };
  const ms = (body.milestones ?? []).filter((m) => typeof m.id === "string" && typeof m.type === "string");
  const m = ms.find((x) => Array.isArray(x.primary_event_tickers) && x.primary_event_tickers.includes(game)) ?? ms[0];
  if (!m || !String(m.type).startsWith("soccer")) return null;
  return { id: String(m.id), type: String(m.type), title: typeof m.title === "string" ? m.title : "" };
}

/** Each requested game ticker's live state ("k:<ticker>"), for the games Kalshi has soccer live data for. */
export async function loadKalshiLive(req: { k: string; names: Set<string> }[]): Promise<Record<string, GameLive>> {
  const found = await Promise.all(req.map(async (r) => {
    try {
      return { ...r, m: await milestoneFor(r.k) };
    } catch (e) {
      console.error(`parlays: Kalshi milestone failed (${r.k}): ${(e as Error).message}`);
      return { ...r, m: null };
    }
  }));
  const withM = found.filter((x): x is typeof x & { m: Milestone } => x.m !== null);
  const out: Record<string, GameLive> = {};
  for (let i = 0; i < withM.length; i += 100) {
    const chunk = withM.slice(i, i + 100);
    try {
      const q = chunk.map((x) => `milestone_ids=${encodeURIComponent(x.m.id)}`).join("&");
      const body = (await getJson(`/live_data/batch?${q}`, 15)) as {
        live_datas?: { milestone_id?: unknown; details?: unknown }[];
      };
      const byId = new Map((body.live_datas ?? []).map((x) => [x.milestone_id, x.details]));
      for (const x of chunk) {
        const d = byId.get(x.m.id);
        if (d && typeof d === "object") out[`k:${x.k}`] = liveFromKalshi(d as Details, x.m.title, x.names);
      }
    } catch (e) {
      console.error(`parlays: Kalshi live data failed: ${(e as Error).message}`);
    }
  }
  return out;
}
