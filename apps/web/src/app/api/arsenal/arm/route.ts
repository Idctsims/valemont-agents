import { headers } from "next/headers";
import { Output, streamText } from "ai";

import { languageModel, usageOf } from "@/lib/ai/claude";
import { checkBudget, recordUsage, type BudgetCheck } from "@/lib/ai/budget";
import { mockAllowed } from "@/lib/ai/policy";
import { CHEAPEST_MODEL, type Usage } from "@/lib/ai/pricing";
import {
  armCardSchema, armPrompt, armProviderOptions, armSystem, ArmSlate, ASK_CARDS, FILL_SPARE, fillPrompt, MAX_SITUATION,
  rankArsenal, ROOMS, SLOW_REQUEST_MS, validateArmCard,
  type ArmCard, type ArmLine, type ArsenalEntry,
} from "@/lib/arsenal/arm";
import type { Category, Room } from "@/lib/arsenal/types";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

// POST /api/arsenal/arm: Arm Me (Chat 5 Phase 2; docs/arsenal.md §9.3).
//
//   1. owner, input (≤ 600 characters, an optional room), the e2e marker (a
//      verified one makes the request and its new cards test rows; the mock
//      model runs only with one, so no e2e run ever writes a real card),
//      the rate limit (10 requests in a rolling minute, the database's
//      clock), the budget (Sonnet; Haiku from 80%; refused at 100%);
//   2. the prompt: the Arsenal voice, the weights, the owner's Arsenal (its
//      ARSENAL_CAP most relevant entries, rankArsenal; owner, 2026-11-03);
//   3. stream Output.array of ASK_CARDS (4): each card, as soon as it is
//      complete, is upserted by canonical_key (db/059 arsenal_arm_card: an
//      existing key comes back as it is, with its real tag) and written as
//      one NDJSON line, until SHOW_CARDS (3) are shown (ArmSlate: at most one
//      "From your Arsenal" card, no duplicate, no invalid card);
//   4. ALWAYS 3 (owner, 2026-11-03): a slot still empty after the stream is
//      filled by one more call for fresh cards that avoids the keys shown;
//   5. at the end: ai_usage (real calls; both calls in one row), then one
//      arm_requests row with first_card_ms and total_ms (server clocks from
//      the request's start), the cards shown, and any error. A refusal
//      before the model writes no row.
// Refusals are plain sentences with a 4xx/5xx status: the page shows them.

export const maxDuration = 60;

const ARM_MODEL = "claude-sonnet-5-5";
const RATE_LIMIT = 10;
const RATE_WINDOW_S = 60;
const MAX_OUTPUT_TOKENS = 2600;

const refuse = (status: number, message: string) =>
  new Response(message, { status, headers: { "content-type": "text/plain; charset=utf-8" } });

type Rpc = { data: unknown; error: { message: string } | null };

export async function POST(req: Request) {
  // The clock for first_card_ms and total_ms starts here, before anything
  // else: auth, the rate limit, the budget, the prompt reads and the model
  // all count, up to the moment card 1's line is written to the response.
  const started = performance.now();
  const user = await requireOwner();
  const supabase = await createClient();
  const mock = mockAllowed(process.env);

  const marker = decideMarker((await headers()).get(MARKER_HEADER), process.env);
  if (marker.kind === "refused") return refuse(403, `Refused: ${marker.reason}.`);
  const markerArg = marker.kind === "test" ? marker.marker : null;
  if (mock && !markerArg) return refuse(400, "Arm Me's mock model runs only with the e2e marker.");

  let body: { situation?: unknown; room?: unknown };
  try {
    body = await req.json();
  } catch {
    return refuse(400, "That request didn't make sense. Try again.");
  }
  const situation = typeof body.situation === "string" ? body.situation.trim() : "";
  if (!situation) return refuse(400, "Describe the room first.");
  if (situation.length > MAX_SITUATION) {
    return refuse(413, `That's ${situation.length} characters. Keep it under ${MAX_SITUATION}.`);
  }
  const room = (ROOMS as readonly string[]).includes(String(body.room)) ? (body.room as Room) : null;
  if (body.room != null && body.room !== "" && !room) return refuse(400, "That isn't one of the four rooms.");

  // Rate limit, by the database's clock. Local e2e may lower it (mock only).
  const limit = mock ? Number(req.headers.get("x-wags-e2e-rate-limit")) || RATE_LIMIT : RATE_LIMIT;
  const recent = await supabase.rpc("arsenal_arm_requests_since", { p_seconds: RATE_WINDOW_S });
  if (recent.error) return refuse(503, "Arm Me couldn't check the rate limit. Try again in a moment.");
  if ((recent.data as number) >= limit) {
    return refuse(429, `That's ${limit} in a minute. Give it a moment, then arm up again.`);
  }

  // Budget (non-critical). Local e2e may force a state (mock only).
  let budget: BudgetCheck;
  try {
    budget = await checkBudget(supabase, user.id, { model: ARM_MODEL, critical: false });
  } catch (e) {
    console.error("arm budget:", e);
    return refuse(503, "Arm Me couldn't read this month's AI budget. Try again in a moment.");
  }
  const forced = mock ? req.headers.get("x-wags-e2e-budget") : null;
  if (forced === "over") {
    budget = { kind: "refuse", message: "This month's AI budget is spent ($20.00 of $20.00). Arm Me is back on the 1st, or when the budget is raised.", spentUsd: 20, budgetUsd: 20 };
  } else if (forced === "downgrade") {
    budget = { kind: "downgrade", model: CHEAPEST_MODEL, spentUsd: 16.5, budgetUsd: 20 };
  }
  if (budget.kind === "refuse") return refuse(402, budget.message.replace("Wags is back", "Arm Me is back"));
  const modelId = budget.model;

  // What the prompt knows: the weights and the owner's Arsenal (real rows).
  const [weights, everything] = await Promise.all([armWeights(supabase), ownerArsenal(supabase)]);
  // The most relevant ARSENAL_CAP only (owner, 2026-11-03: input tokens).
  const arsenal = rankArsenal(everything, situation, weights, room);

  let model;
  try {
    model = await languageModel(modelId, "arsenal");
  } catch (e) {
    return refuse(503, e instanceof Error ? e.message : "Arm Me is not configured.");
  }

  const system = armSystem({ weights, arsenal });
  const ask = (prompt: string, count: number) => streamText({
    model: model.model,
    system,
    prompt,
    output: Output.array({ element: armCardSchema, name: "cards", description: `Exactly ${count} Arsenal cards, best first.` }),
    maxOutputTokens: MAX_OUTPUT_TOKENS,
    // Thinking at its lowest, explicitly (arm.ts armProviderOptions = Wags').
    providerOptions: armProviderOptions(modelId),
    abortSignal: req.signal,
  });
  const result = ask(armPrompt(situation, room), ASK_CARDS);

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let open = true;
      const send = (line: ArmLine) => {
        if (!open) return;
        try {
          controller.enqueue(encoder.encode(`${JSON.stringify(line)}\n`));
        } catch {
          open = false; // the phone went away; keep going so the request is recorded
        }
      };
      send({ type: "start", model: model.id, budgetMode: budget.kind === "downgrade" });

      const slate = new ArmSlate();
      const calls = [result];
      let firstCardMs: number | null = null;
      let error: string | null = null;

      // One call's cards into the slate, each shown the moment it passes.
      // Read to the end (never break: the usage resolves when the stream does).
      const take = async (call: typeof result) => {
        for await (const element of call.elementStream) {
          if (slate.full) {
            slate.skip(`a spare card (${String(element.canonical_key)}) was not needed`);
            continue;
          }
          const checked = validateArmCard(element);
          if (!checked.card) {
            slate.skip(`left out: ${checked.why}`);
            continue;
          }
          const { data, error: rpcError } = (await supabase.rpc("arsenal_arm_card", {
            p_card: checked.card, p_marker: markerArg,
          })) as Rpc;
          if (rpcError) {
            slate.skip(`not stored (${checked.card.canonical_key}): ${rpcError.message}`);
            continue;
          }
          const card = data as ArmCard | null;
          if (!card) {
            slate.skip(`${checked.card.canonical_key} belongs to the other world (real vs test)`);
            continue;
          }
          // The Source link: the stored one (a verified card's fetched page),
          // else the model's. db/059 does not store the model's, so a saved
          // card's link in My Arsenal is the verifier's once it settles.
          if (!card.source_url) card.source_url = checked.card.source_url;
          if (!slate.offer(card)) continue;
          firstCardMs ??= Math.round(performance.now() - started);
          send({ type: "card", card });
        }
      };

      try {
        await take(result);
        // Always three (owner, 2026-11-03): a slot left empty by a second
        // From-your-Arsenal card, a duplicate or an invalid one is filled by
        // one more call for fresh cards, never shown as two.
        if (!slate.full && !req.signal.aborted) {
          const missing = slate.missing;
          const avoid = [...slate.keys(), ...arsenal.map((e) => e.key)];
          const fill = ask(fillPrompt(situation, room, missing + FILL_SPARE, avoid), missing + FILL_SPARE);
          calls.push(fill);
          await take(fill);
          slate.skip(`filled ${missing} slot(s) with a second call`);
        }
        if (slate.shown.length === 0) error = "no usable card came back";
        else if (!slate.full) error = `only ${slate.shown.length} card(s) after the fill-up call`;
      } catch (e) {
        error = req.signal.aborted ? "aborted by the client" : e instanceof Error ? e.message : String(e);
        console.error("arm stream:", error);
      }
      const shown = slate.shown;
      const notes = slate.notes;
      const totalMs = Math.round(performance.now() - started);
      // The owner's bar (2026-11-03): well under a minute. A miss is said in the row.
      if (totalMs > SLOW_REQUEST_MS) {
        notes.unshift(`slower than ${SLOW_REQUEST_MS / 1000} s (${totalMs} ms)`);
        console.warn(`arm: ${totalMs} ms (bar ${SLOW_REQUEST_MS} ms)`);
      }

      // Spend first: a call that is not on record is spend the budget cannot see.
      // Both calls (the fill-up, when there is one) in one ai_usage row.
      let usageId: number | null = null;
      if (!model.mock) {
        try {
          const usage: Usage = sumUsage(await Promise.all(calls.map(async (c) => usageOf(await c.totalUsage))));
          usageId = (await recordUsage(supabase, { purpose: "arsenal_arm", model: modelId, usage, critical: false })).id;
        } catch (e) {
          console.error("arm usage:", e);
          notes.push("usage not recorded");
        }
      }
      let requestId: number | null = null;
      const note = notes.length ? notes.join("; ").slice(0, 500) : null;
      const recorded = (await supabase.rpc("arsenal_arm_record", {
        p_situation: situation, p_room_hint: room, p_model: model.id, p_first_card_ms: firstCardMs,
        p_total_ms: totalMs, p_ai_usage_id: usageId, p_ref_ids: shown.map((c) => c.id),
        p_error: error, p_note: note, p_marker: markerArg,
      })) as Rpc;
      if (recorded.error) console.error("arm record:", recorded.error.message);
      else requestId = recorded.data as number;

      if (error && shown.length === 0) {
        send({ type: "error", message: req.signal.aborted ? "Stopped." : "Arm Me came back empty-handed. Try again, or describe the room another way." });
      } else {
        send({ type: "done", requestId, note: error ? "The answer was cut short; these are the cards that arrived." : null });
      }
      if (open) controller.close();
    },
  });

  return new Response(stream, {
    headers: { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store" },
  });
}

function sumUsage(all: Usage[]): Usage {
  return all.reduce((a, u) => ({
    tokensIn: a.tokensIn + u.tokensIn, tokensOut: a.tokensOut + u.tokensOut, cacheRead: a.cacheRead + u.cacheRead,
    cacheWrite5m: a.cacheWrite5m + u.cacheWrite5m, cacheWrite1h: (a.cacheWrite1h ?? 0) + (u.cacheWrite1h ?? 0),
  }), { tokensIn: 0, tokensOut: 0, cacheRead: 0, cacheWrite5m: 0, cacheWrite1h: 0 });
}

/** "category:film_tv" -> 1.15 (db/059 arsenal_weights, saves included). */
async function armWeights(supabase: Awaited<ReturnType<typeof createClient>>): Promise<Record<string, number>> {
  const { data, error } = await supabase.rpc("arsenal_weights");
  if (error) {
    console.error("arm weights:", error.message);
    return {};
  }
  return Object.fromEntries(((data ?? []) as { dim: string; key: string; weight: number }[])
    .map((w) => [`${w.dim}:${w.key}`, Number(w.weight)]));
}

type RefRow = { id: number; canonical_key: string; title: string; status: string; category: Category; room: Room };

/** The owner's whole Arsenal (real rows): seeds, dropped and saved
 *  references, with category and room for rankArsenal. A saved one is
 *  tagged saved, whatever its status. */
async function ownerArsenal(supabase: Awaited<ReturnType<typeof createClient>>): Promise<ArsenalEntry[]> {
  const cols = "id, canonical_key, title, status, category, room";
  const [owned, saved] = await Promise.all([
    supabase.from("arsenal_refs").select(cols).in("status", ["seed", "dropped"])
      .eq("is_test", false).order("id").limit(200),
    supabase.from("v_arsenal_saved").select("ref_id").not("ref_id", "is", null).eq("is_test", false).limit(200),
  ]);
  if (owned.error) console.error("arm arsenal:", owned.error.message);
  const savedIds = new Set(((saved.data ?? []) as { ref_id: number }[]).map((r) => r.ref_id));
  const entry = (r: RefRow): ArsenalEntry => ({
    key: r.canonical_key, title: r.title, category: r.category, room: r.room, id: r.id,
    kind: savedIds.has(r.id) ? "saved" : (r.status as "seed" | "dropped"),
  });
  const entries = ((owned.data ?? []) as RefRow[]).map(entry);
  const have = new Set(entries.map((e) => e.id));
  const rest = [...savedIds].filter((id) => !have.has(id));
  if (rest.length) {
    const rows = await supabase.from("arsenal_refs").select(cols).in("id", rest);
    entries.push(...((rows.data ?? []) as RefRow[]).map(entry));
  }
  return entries;
}
