import { headers } from "next/headers";
import { Output, generateText } from "ai";

import { checkBudget, recordUsage, type BudgetCheck } from "@/lib/ai/budget";
import { languageModel, usageOf } from "@/lib/ai/claude";
import { mockAllowed } from "@/lib/ai/policy";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { titleStates, type TitleState } from "@/lib/screening/data";
import {
  CACHE_DAYS, RATE_LIMIT, RATE_WINDOW_S, VIBE_MODEL, anchorKey, anchorLabel, finalize, matchPick, parseAnchor,
  vibePrompt, vibeSchema, vibeSystem, type Anchor, type VibeCard, type VibePick,
} from "@/lib/screening/vibe";
import { createClient } from "@/lib/supabase/server";
import { TmdbTokenError, searchKind, titleProviders } from "@/lib/tmdb/client";
import { splitProviders } from "@/lib/tmdb/services";

// POST /api/screening/vibe: More like… (owner, 2026-11-21; docs/screening.md §8).
//
//   1. owner; the e2e marker (a verified one makes the search a test row;
//      the mock model runs only with one); the anchor (screening/vibe.ts);
//   2. the cache: the newest row for the same anchor_key and is_test within
//      CACHE_DAYS is answered as it is, no model call, no TMDB call;
//   3. the rate limit (RATE_LIMIT stored searches in a rolling RATE_WINDOW_S,
//      the database's clock) and the budget (Sonnet; Haiku from 80%; refused
//      at 100%);
//   4. Sonnet names ASK_PICKS titles (Output.object); each is matched on TMDB
//      by kind, name and year (a year-free retry when the year misses) and
//      the unmatched are dropped (finalize); each kept title's US providers
//      name the owner's services;
//   5. ai_usage (real calls), then one screening_vibe_searches row (db/088);
//      none matched is a 502 and stores nothing.
// Every answer adds the owner's state per title live (shelf, seen, reaction).
// Refusals are plain sentences with a 4xx/5xx status: the page shows them.

export const maxDuration = 60;

const MAX_OUTPUT_TOKENS = 2400;

const refuse = (status: number, message: string) =>
  new Response(message, { status, headers: { "content-type": "text/plain; charset=utf-8" } });

type StoredCard = VibeCard & { services: string[] };
type Supabase = Awaited<ReturnType<typeof createClient>>;

/** Each card with the owner's live state (a failed read leaves the cards without it, logged). */
async function withState(supabase: Supabase, cards: StoredCard[], withTests: boolean) {
  let states = new Map<string, TitleState>();
  try {
    states = await titleStates(supabase, cards, { withTests });
  } catch (e) {
    console.error("vibe states:", e instanceof Error ? e.message : e);
  }
  return cards.map((c) => {
    const s = states.get(`${c.kind}:${c.tmdbId}`);
    return { ...c, shelf: s?.shelf ?? null, seen: s?.seen ?? false, reaction: s?.reaction ?? null,
             services: s?.services.length ? s.services : c.services };
  });
}

/** One pick on TMDB: with its year first, then without (a year off by one is still the title). */
async function lookup(p: VibePick) {
  const first = matchPick(p, await searchKind(p.kind, p.title, p.year));
  if (first || p.year == null) return first;
  return matchPick(p, await searchKind(p.kind, p.title, null));
}

async function servicesFor(c: VibeCard): Promise<string[]> {
  try {
    return [...new Set(splitProviders(await titleProviders(c.kind, c.tmdbId)).tonight.map((t) => t.service))].sort();
  } catch {
    return []; // cosmetic: the title page shows where it streams
  }
}

export async function POST(req: Request) {
  const user = await requireOwner();
  const supabase = await createClient();
  const mock = mockAllowed(process.env);

  const marker = decideMarker((await headers()).get(MARKER_HEADER), process.env);
  if (marker.kind === "refused") return refuse(403, `Refused: ${marker.reason}.`);
  const withTests = marker.kind === "test";
  if (mock && !withTests) return refuse(400, "More like…'s mock model runs only with the e2e marker.");

  let body: { anchor?: unknown };
  try {
    body = await req.json();
  } catch {
    return refuse(400, "That search didn't make sense. Try again.");
  }
  const parsed = parseAnchor(body.anchor);
  if (typeof parsed === "string") return refuse(400, parsed);
  const anchor: Anchor = parsed;
  const key = anchorKey(anchor);
  const label = anchorLabel(anchor);

  // 2. The cache.
  const since = new Date(Date.now() - CACHE_DAYS * 86_400_000).toISOString();
  const hit = await supabase.from("screening_vibe_searches").select("label, picks, created_at")
    .eq("anchor_key", key).eq("is_test", withTests).gte("created_at", since)
    .order("created_at", { ascending: false }).limit(1).maybeSingle();
  if (hit.error) console.error("vibe cache:", hit.error.message); // db/088 unpasted, or a read failure: ask Claude
  if (hit.data) {
    const cards = await withState(supabase, hit.data.picks as StoredCard[], withTests);
    return Response.json({ cached: true, label: hit.data.label, at: hit.data.created_at, cards });
  }

  // 3. Rate limit and budget.
  const recent = await supabase.from("screening_vibe_searches").select("id", { count: "exact", head: true })
    .gte("created_at", new Date(Date.now() - RATE_WINDOW_S * 1000).toISOString());
  if ((recent.count ?? 0) >= RATE_LIMIT) {
    return refuse(429, `That's ${RATE_LIMIT} searches in a minute. Give it a moment.`);
  }
  let budget: BudgetCheck;
  try {
    budget = await checkBudget(supabase, user.id, { model: VIBE_MODEL, critical: false });
  } catch (e) {
    console.error("vibe budget:", e);
    return refuse(503, "Couldn't read this month's AI budget. Try again in a moment.");
  }
  if (budget.kind === "refuse") return refuse(402, budget.message.replace("Wags is back", "More like… is back"));
  const modelId = budget.model;

  // 4. The model, then TMDB.
  let model;
  try {
    model = await languageModel(modelId, "vibe");
  } catch (e) {
    return refuse(503, e instanceof Error ? e.message : "More like… is not configured.");
  }
  let picks: VibePick[];
  let usageId: number | null = null;
  try {
    const result = await generateText({
      model: model.model,
      system: vibeSystem(),
      prompt: vibePrompt(anchor),
      output: Output.object({ schema: vibeSchema, name: "vibe", description: "Titles with the same vibe, closest first." }),
      maxOutputTokens: MAX_OUTPUT_TOKENS,
      providerOptions: modelId === "claude-haiku-5-5"
        ? { anthropic: { thinking: { type: "disabled" as const } } }
        : { anthropic: { thinking: { type: "between_tools" as const }, effort: "low" as const } },
      abortSignal: req.signal,
    });
    if (!model.mock) {
      try {
        usageId = (await recordUsage(supabase, { purpose: "screening_vibe", model: modelId, usage: usageOf(result.usage), critical: false })).id;
      } catch (e) {
        console.error("vibe usage:", e); // loud in the log; the owner still gets an answer
      }
    }
    picks = result.output.picks;
  } catch (e) {
    console.error("vibe model:", e instanceof Error ? e.message : e);
    return refuse(502, "Claude didn't answer. Try again.");
  }

  let cards: StoredCard[];
  try {
    const rows = await Promise.all(picks.map(async (pick) => ({ pick, hit: await lookup(pick).catch(() => null) })));
    const kept = finalize(rows, anchor);
    const services = await Promise.all(kept.map(servicesFor));
    cards = kept.map((c, i) => ({ ...c, services: services[i] }));
  } catch (e) {
    const message = e instanceof TmdbTokenError ? e.message : "TMDB didn't answer. Try again.";
    console.error("vibe tmdb:", e instanceof Error ? e.message : e);
    return refuse(502, message);
  }
  if (cards.length === 0) return refuse(502, "Couldn't match any of Claude's picks on TMDB. Try again.");

  // 5. The record (a failure to store still answers; the next search asks again).
  const stored = await supabase.from("screening_vibe_searches").insert({
    anchor_key: key, label, picks: cards, model: model.mock ? "mock" : modelId, ai_usage_id: usageId, is_test: withTests,
  });
  if (stored.error) console.error("vibe store:", stored.error.message);

  return Response.json({ cached: false, label, at: new Date().toISOString(), cards: await withState(supabase, cards, withTests) });
}
