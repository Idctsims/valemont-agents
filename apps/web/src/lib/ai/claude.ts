import "server-only";

import { createAnthropic } from "@ai-sdk/anthropic";
import { generateText, type LanguageModel, type LanguageModelUsage } from "ai";
import type { SupabaseClient } from "@supabase/supabase-js";

import { checkBudget, recordUsage } from "./budget";
import { mockAllowed } from "./policy";
import { CHEAPEST_MODEL, type Usage } from "./pricing";

// The ONLY module in apps/web that talks to Anthropic (pnpm check:ai-import
// fails on any other importer of @ai-sdk/anthropic or @anthropic-ai/sdk).
// Every call made from here is costed and written to ai_usage by budget.ts.

export type WagsModel = { model: LanguageModel; id: string; mock: boolean };

/** The real model, or the e2e mock when (and only when) policy.mockAllowed says so. */
export async function languageModel(id: string): Promise<WagsModel> {
  if (mockAllowed(process.env)) {
    const { mockModel } = await import("@/lib/wags/mock");
    return { model: mockModel(), id: "mock", mock: true };
  }
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error("ANTHROPIC_API_KEY is not set, so Wags cannot answer. Add it to Vercel (or apps/web/.env.local).");
  return { model: createAnthropic({ apiKey: key })(id), id, mock: false };
}

/**
 * Chat settings per model. Thinking off, so the answer gets the whole output
 * budget: Sonnet 5.5 turns thinking off with `between_tools` (it rejects
 * `disabled`), Haiku 5.5 accepts `disabled` at its default effort.
 */
export function chatProviderOptions(id: string) {
  return {
    anthropic: {
      thinking: id === CHEAPEST_MODEL ? ({ type: "disabled" } as const) : ({ type: "between_tools" } as const),
    },
  };
}

/** The SDK's usage, in pricing.ts's terms (every write here is the 5-minute TTL). */
export function usageOf(u: LanguageModelUsage): Usage {
  const read = u.inputTokenDetails?.cacheReadTokens ?? 0;
  const write = u.inputTokenDetails?.cacheWriteTokens ?? 0;
  const fresh = u.inputTokenDetails?.noCacheTokens ?? Math.max((u.inputTokens ?? 0) - read - write, 0);
  return { tokensIn: fresh, tokensOut: u.outputTokens ?? 0, cacheRead: read, cacheWrite5m: write };
}

/**
 * A thread title from the first exchange, by Haiku, recorded in ai_usage as
 * 'wags_title'. Falls back to the first words of the question when the
 * budget refuses, in mock mode, or when the call fails: a title is never
 * worth an error.
 */
export async function titleFor(
  supabase: SupabaseClient,
  ownerId: string,
  question: string,
  answer: string,
): Promise<string> {
  const fallback = fallbackTitle(question);
  try {
    const { model, id, mock } = await languageModel(CHEAPEST_MODEL);
    if (mock) return fallback;
    const budget = await checkBudget(supabase, ownerId, { model: id, critical: false });
    if (budget.kind === "refuse") return fallback;
    const result = await generateText({
      model,
      maxOutputTokens: 30,
      providerOptions: chatProviderOptions(CHEAPEST_MODEL),
      system:
        "Write a title of 2 to 6 words for this conversation. Plain text, no quotes, no trailing period. Name the subject, not the question.",
      prompt: `Question: ${question.slice(0, 600)}\n\nAnswer: ${answer.slice(0, 600)}`,
    });
    await recordUsage(supabase, {
      purpose: "wags_title",
      model: id,
      usage: usageOf(result.usage),
      critical: false,
    });
    const title = result.text.trim().replace(/^["']|["'.]$/g, "").slice(0, 120);
    return title || fallback;
  } catch (e) {
    console.error("wags title:", e instanceof Error ? e.message : e);
    return fallback;
  }
}

export function fallbackTitle(question: string): string {
  const words = question.trim().replace(/\s+/g, " ").split(" ").slice(0, 6).join(" ");
  return (words.length > 60 ? `${words.slice(0, 59)}…` : words) || "New thread";
}
