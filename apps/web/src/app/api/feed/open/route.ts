import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

// POST /api/feed/open: the beacon a card sends (navigator.sendBeacon) when
// the owner opens its publisher. Writes one 'open' row (db/039
// feed_reactions), a weak Love signal (0.3) in the next profile rebuild.
//
//   * owner only (requireOwner; RLS checks owner_id again);
//   * same origin only (a beacon carries cookies; the Origin header must be
//     this host);
//   * at most RATE_LIMIT opens a minute;
//   * one open per story and pillar per DEDUPE_MIN minutes: reopening the
//     same story is not more signal.
//
// Always 204 on an accepted or deduped beacon: the page has already gone to
// the publisher and nobody reads the answer. 4xx with a plain sentence
// otherwise, for the tests.

const RATE_LIMIT = 60;
const DEDUPE_MIN = 10;
const PILLARS = new Set(["home", "sports"]);

const answer = (status: number, message?: string) =>
  new Response(message ?? null, { status, headers: message ? { "content-type": "text/plain; charset=utf-8" } : {} });

export async function POST(req: Request) {
  await requireOwner();
  const origin = req.headers.get("origin");
  if (origin && origin !== new URL(req.url).origin) return answer(403, "Cross-origin beacons are refused.");

  let body: { storyId?: unknown; pillar?: unknown };
  try {
    body = JSON.parse(await req.text());
  } catch {
    return answer(400, "That beacon didn't make sense.");
  }
  const storyId = Number(body.storyId);
  const pillar = String(body.pillar ?? "");
  if (!Number.isSafeInteger(storyId) || storyId < 1 || !PILLARS.has(pillar)) {
    return answer(400, "That beacon didn't name a story and a feed.");
  }

  const supabase = await createClient();
  const minuteAgo = new Date(Date.now() - 60_000).toISOString();
  const recent = await supabase
    .from("feed_reactions")
    .select("id", { count: "exact", head: true })
    .eq("reaction", "open")
    .gte("created_at", minuteAgo);
  if (recent.error) return answer(503, "Couldn't check the rate limit.");
  if ((recent.count ?? 0) >= RATE_LIMIT) return answer(429, `That's ${RATE_LIMIT} opens in a minute.`);

  const since = new Date(Date.now() - DEDUPE_MIN * 60_000).toISOString();
  const dupe = await supabase
    .from("feed_reactions")
    .select("id", { count: "exact", head: true })
    .eq("reaction", "open")
    .eq("story_id", storyId)
    .eq("pillar", pillar)
    .gte("created_at", since);
  if (dupe.error) return answer(503, "Couldn't check for a repeat open.");
  if ((dupe.count ?? 0) > 0) return answer(204);

  const { error } = await supabase.from("feed_reactions").insert({ story_id: storyId, pillar, reaction: "open" });
  if (error) return answer(error.code === "23503" ? 404 : 400, "Couldn't record the open.");
  return answer(204);
}
