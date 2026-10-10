import { convertToModelMessages, streamText, type SystemModelMessage } from "ai";

import { chatProviderOptions, fallbackTitle, languageModel, titleFor, usageOf } from "@/lib/ai/claude";
import { checkBudget, recordUsage, type BudgetCheck } from "@/lib/ai/budget";
import { mockAllowed } from "@/lib/ai/policy";
import { costUsd, CHEAPEST_MODEL, WAGS_MODEL, type Usage } from "@/lib/ai/pricing";
import { requireOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { forModel, loadThread } from "@/lib/wags/history";
import { pageSlice, parsePageContext } from "@/lib/wags/page-context";
import { PERSONA, PERSONA_VERSION } from "@/lib/wags/persona";
import { wagsTools, type WagsMetadata, type WagsUIMessage } from "@/lib/wags/tools";

// POST /api/wags: one turn of a Wags thread, streamed.
//
//   1. owner, input, rate limit (12 user messages per rolling minute, counted
//      by the database), budget (src/lib/ai/budget.ts: Sonnet, Haiku from 80%,
//      refused at 100%);
//   2. the user message is stored, then a context snapshot is built and
//      recorded (db/029 context_snapshot_record), and the thread is rebuilt
//      from the database (never from what the client sends);
//   3. stream. System = [persona, context]; the cache breakpoint sits on the
//      last system block, so both are cached. The clock and the page go on the
//      newest user turn only, after the breakpoint.
//   4. on finish (or abort), the answer is stored with its model, tokens,
//      cost and snapshot id; a stream cut short keeps what arrived, marked
//      incomplete.
//
// Refusals are plain sentences with a 4xx status: the client shows the body.

// Vercel Hobby's maximum with Fluid compute (verified 2026-10-10,
// vercel.com/docs/functions/configuring-functions/duration).
export const maxDuration = 300;

const RATE_LIMIT = 12;
const RATE_WINDOW_S = 60;
const MAX_INPUT = 4000;
const MAX_OUTPUT_TOKENS = 1200;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const refuse = (status: number, message: string) =>
  new Response(message, { status, headers: { "content-type": "text/plain; charset=utf-8" } });

const CLOCK_KEYS = ["built_at", "local_date", "local_time", "weekday", "iso_week", "timezone"] as const;

function textOf(message: unknown): string {
  const parts = (message as { parts?: unknown[] } | null)?.parts;
  if (!Array.isArray(parts)) return "";
  return parts
    .map((p) => (p && typeof p === "object" && (p as { type?: string }).type === "text" ? String((p as { text?: unknown }).text ?? "") : ""))
    .join("")
    .trim();
}

export async function POST(req: Request) {
  const user = await requireOwner();
  const supabase = await createClient();
  const mock = mockAllowed(process.env);

  let body: { threadId?: unknown; message?: unknown; page?: unknown };
  try {
    body = await req.json();
  } catch {
    return refuse(400, "That request didn't make sense. Try again.");
  }
  const threadId = typeof body.threadId === "string" && UUID.test(body.threadId) ? body.threadId : null;
  if (!threadId) return refuse(400, "That thread doesn't exist.");
  const text = textOf(body.message);
  if (!text) return refuse(400, "Say something first.");
  if (text.length > MAX_INPUT) {
    return refuse(413, `That's ${text.length.toLocaleString("en-US")} characters. Keep it under ${MAX_INPUT.toLocaleString("en-US")}.`);
  }
  const page = parsePageContext(body.page);

  // Rate limit, by the database's clock. Local e2e may lower it (mock only).
  const limit = mock ? Number(req.headers.get("x-wags-e2e-rate-limit")) || RATE_LIMIT : RATE_LIMIT;
  const recent = await supabase.rpc("wags_user_messages_since", { p_seconds: RATE_WINDOW_S });
  if (recent.error) return refuse(503, "Wags couldn't check the rate limit. Try again in a moment.");
  if ((recent.data as number) >= limit) {
    return refuse(429, `That's ${limit} message${limit === 1 ? "" : "s"} in a minute. Give it a moment, then ask again.`);
  }

  // Budget. Local e2e may force a state (mock only); nothing real is touched.
  let budget: BudgetCheck;
  try {
    budget = await checkBudget(supabase, user.id, { model: WAGS_MODEL, critical: false });
  } catch (e) {
    console.error("wags budget:", e);
    return refuse(503, "Wags couldn't read this month's AI budget. Try again in a moment.");
  }
  const forced = mock ? req.headers.get("x-wags-e2e-budget") : null;
  if (forced === "over") {
    budget = { kind: "refuse", message: "This month's AI budget is spent ($20.00 of $20.00). Wags is back on the 1st, or when the budget is raised.", spentUsd: 20, budgetUsd: 20 };
  } else if (forced === "downgrade") {
    budget = { kind: "downgrade", model: CHEAPEST_MODEL, spentUsd: 16.5, budgetUsd: 20 };
  }
  if (budget.kind === "refuse") return refuse(402, budget.message);
  const modelId = budget.model;
  const budgetMode = budget.kind === "downgrade";

  // The thread: created on its first message, with the page it began on.
  const { thread } = await loadThread(supabase, threadId).catch(() => ({ thread: null }));
  if (!thread) {
    const { error } = await supabase.from("wags_threads").insert({ id: threadId, origin_page: page?.route ?? null });
    if (error) return refuse(400, "Wags couldn't start that thread.");
  }

  const stored = await supabase
    .from("wags_messages")
    .insert({
      thread_id: threadId,
      role: "user",
      content: text,
      parts: [{ type: "text", text }],
      page_context: page,
    })
    .select("id")
    .single();
  if (stored.error) return refuse(400, `Wags couldn't save that message: ${stored.error.message}`);

  // Recorded, so what Wags knew stays on file. Local e2e (mock) only reads:
  // snapshots are append-only, and its "e2e …" rows would be there forever.
  const snap = mock ? await supabase.rpc("context_snapshot") : await supabase.rpc("context_snapshot_record");
  if (snap.error) return refuse(503, "Wags couldn't read your pillars just now. Try again in a moment.");
  const { id: snapshotId, payload } = mock
    ? { id: null, payload: snap.data as Record<string, unknown> }
    : (snap.data as { id: number; payload: Record<string, unknown> });

  let model;
  try {
    model = await languageModel(modelId);
  } catch (e) {
    return refuse(503, e instanceof Error ? e.message : "Wags is not configured.");
  }

  const { messages: history } = await loadThread(supabase, threadId);
  const context = Object.fromEntries(Object.entries(payload).filter(([k]) => !(CLOCK_KEYS as readonly string[]).includes(k)));
  const system: SystemModelMessage[] = [
    { role: "system", content: PERSONA },
    {
      role: "system",
      content: `CONTEXT: the live state of Tsims's pillars, built by the database when he sent his latest message.\n${JSON.stringify(context)}`,
      providerOptions: { anthropic: { cacheControl: { type: "ephemeral" } } },
    },
  ];

  // The volatile note rides on the newest user turn only, so every earlier
  // turn (and both system blocks) stays byte-identical and cached.
  const note = [
    `[Now: ${payload.weekday}, ${payload.local_date} ${payload.local_time} ${payload.timezone} · ISO week ${payload.iso_week}]`,
    page ? `[Opened from: ${page.label}]` : null,
    page?.pillar ? `[Page detail: ${JSON.stringify(pageSlice(payload, page))}]` : null,
  ]
    .filter(Boolean)
    .join("\n");
  const modelCopy = forModel(history);
  const last = modelCopy[modelCopy.length - 1];
  if (last?.role === "user") last.parts = [{ type: "text", text: `${note}\n\n${text}` }];

  // A new thread is titled from its first question while Wags answers.
  const title = !thread?.title && history.filter((m) => m.role === "user").length === 1
    ? titleFor(supabase, user.id, text, "").catch(() => fallbackTitle(text))
    : null;

  // What Anthropic reported at message_start, for a stream that is cut short.
  const seen = { tokensIn: 0, cacheRead: 0, cacheWrite5m: 0 };
  let failed = false;

  const result = streamText({
    model: model.model,
    system,
    messages: await convertToModelMessages(modelCopy, { tools: wagsTools }),
    tools: wagsTools,
    maxOutputTokens: MAX_OUTPUT_TOKENS,
    providerOptions: chatProviderOptions(modelId),
    abortSignal: req.signal,
    includeRawChunks: true,
    onChunk: ({ chunk }) => {
      if (chunk.type !== "raw") return;
      const v = chunk.rawValue as { type?: string; message?: { usage?: Record<string, number> } } | null;
      if (v?.type !== "message_start" || !v.message?.usage) return;
      const u = v.message.usage;
      seen.tokensIn = u.input_tokens ?? 0;
      seen.cacheRead = u.cache_read_input_tokens ?? 0;
      seen.cacheWrite5m = u.cache_creation_input_tokens ?? 0;
    },
  });
  // Keep consuming if the phone drops the connection, so onFinish still runs.
  void result.consumeStream();

  const metadataFor = (extra: WagsMetadata = {}): WagsMetadata => ({ model: model.id, budgetMode, ...extra });

  return result.toUIMessageStreamResponse<WagsUIMessage>({
    originalMessages: history,
    generateMessageId: () => `a${crypto.randomUUID()}`,
    messageMetadata: ({ part }) => (part.type === "start" ? metadataFor() : undefined),
    onError: (error) => {
      failed = true;
      console.error("wags stream:", error);
      return "Wags lost the thread mid-answer. What arrived is saved; ask again to continue.";
    },
    onFinish: async ({ responseMessage, isAborted }) => {
      const answer = responseMessage.parts
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("")
        .trim();
      let incomplete = isAborted || failed;
      // A stream stopped before Anthropic's closing usage: input is what
      // message_start reported; output is estimated at ~4 characters a token.
      let usage: Usage = { ...seen, tokensOut: Math.ceil(answer.length / 4) };
      if (!incomplete) {
        try {
          usage = usageOf(await result.totalUsage);
        } catch (e) {
          console.error("wags usage read:", e);
          incomplete = true;
        }
      }
      let usageRow: { id: number; costUsd: number } | null = null;
      try {
        if (!model.mock) {
          usageRow = await recordUsage(supabase, { purpose: "wags_chat", model: modelId, usage, critical: false });
        }
      } catch (e) {
        console.error("wags usage:", e);
      }
      const { error } = await supabase.from("wags_messages").insert({
        thread_id: threadId,
        role: "assistant",
        content: answer,
        parts: responseMessage.parts,
        context_snapshot_id: snapshotId,
        model: model.id,
        persona_version: PERSONA_VERSION,
        tokens_in: usage.tokensIn,
        tokens_out: usage.tokensOut,
        cache_read: usage.cacheRead,
        cache_write: usage.cacheWrite5m,
        cost_usd: model.mock ? 0 : (usageRow?.costUsd ?? costUsd(modelId, usage)),
        ai_usage_id: usageRow?.id ?? null,
        incomplete,
      });
      if (error) console.error("wags save:", error.message);
      if (title) {
        const t = await title;
        await supabase.from("wags_threads").update({ title: t }).eq("id", threadId).is("title", null);
      }
    },
  });
}
