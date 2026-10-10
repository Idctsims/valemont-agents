import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";

import { pushToOwner } from "@/lib/push/send";

import { alertDue, decide, monthlyBudget, type BudgetDecision } from "./policy";
import { costUsd, type Usage } from "./pricing";

// The web twin of workers/core/ai.py's budget guard. Spend and the alert
// state come from ai_budget_state() (db/029): the same ai_usage rows, the same
// owner's-month boundary and the same notifications kinds the worker reads,
// so neither side sends an alert the other already sent. Everything here runs
// as the owner, through RLS.

export type BudgetCheck = BudgetDecision & { spentUsd: number; budgetUsd: number };

type BudgetState = { spent_usd: number | string; alerted_80: boolean; alerted_100: boolean };

const ALERTS = {
  ai_budget_80: (spent: number, budget: number) => ({
    title: "AI budget at 80%",
    body: `$${spent.toFixed(2)} of $${budget.toFixed(2)} this month. Non-critical AI calls now use Haiku.`,
  }),
  ai_budget_100: (spent: number, budget: number) => ({
    title: "AI budget spent",
    body: `$${spent.toFixed(2)} of $${budget.toFixed(2)} this month. Non-critical AI calls are refused until the 1st; critical ones still run.`,
  }),
} as const;

export async function budgetState(supabase: SupabaseClient): Promise<{ spentUsd: number; budgetUsd: number; state: BudgetState }> {
  const { data, error } = await supabase.rpc("ai_budget_state");
  if (error) throw new Error(`Couldn't read the AI budget: ${error.message}`);
  const state = data as BudgetState;
  return { spentUsd: Number(state.spent_usd), budgetUsd: monthlyBudget(process.env), state };
}

/**
 * Apply the budget to one call, and send the month's 80% / 100% alert if it
 * is due and nobody (web or worker) has sent it yet. A failed push never
 * blocks the call; it is logged and tried again on the next one.
 */
export async function checkBudget(
  supabase: SupabaseClient,
  ownerId: string,
  call: { model: string; critical: boolean },
): Promise<BudgetCheck> {
  const { spentUsd, budgetUsd, state } = await budgetState(supabase);
  const due = alertDue(spentUsd, budgetUsd, state);
  if (due) {
    try {
      await pushToOwner(supabase, ownerId, {
        kind: due,
        ...ALERTS[due](spentUsd, budgetUsd),
        deepLink: "/settings/health",
        tag: "ai-budget",
      });
    } catch (e) {
      console.error(`budget alert ${due} not sent:`, e instanceof Error ? e.message : e);
    }
  }
  return { ...decide(spentUsd, budgetUsd, call.model, call.critical), spentUsd, budgetUsd };
}

/** One ai_usage row for one call, costed by pricing.ts. Returns its id and cost. */
export async function recordUsage(
  supabase: SupabaseClient,
  row: { purpose: string; model: string; usage: Usage; critical: boolean },
): Promise<{ id: number; costUsd: number }> {
  const cost = costUsd(row.model, row.usage);
  const { data, error } = await supabase
    .from("ai_usage")
    .insert({
      purpose: row.purpose,
      model: row.model,
      tokens_in: row.usage.tokensIn,
      tokens_out: row.usage.tokensOut,
      cache_read_tokens: row.usage.cacheRead,
      cache_write_tokens: row.usage.cacheWrite5m + (row.usage.cacheWrite1h ?? 0),
      cost_usd: cost,
      critical: row.critical,
    })
    .select("id")
    .single();
  // Spend that is not on record is spend the budget cannot see: say so loudly.
  if (error) throw new Error(`Couldn't record AI usage (${row.purpose}, $${cost}): ${error.message}`);
  return { id: data.id as number, costUsd: cost };
}
