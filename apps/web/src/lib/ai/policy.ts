// Pure rules shared by the web's Claude calls; no server imports, so the
// unit specs (e2e/ai-unit.spec.ts) run them without a server.

import { CHEAPEST_MODEL } from "./pricing";

export const DEFAULT_BUDGET_USD = 10;
export const DOWNGRADE_AT = 0.8;

/** core/ai.py monthly_budget: blank means the default; anything else must be a positive number. */
export function monthlyBudget(env: Record<string, string | undefined>): number {
  const raw = (env.AI_MONTHLY_BUDGET_USD ?? "").trim();
  if (!raw) return DEFAULT_BUDGET_USD;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`AI_MONTHLY_BUDGET_USD must be a positive number of dollars, got "${raw}".`);
  }
  return value;
}

export type BudgetDecision =
  | { kind: "normal"; model: string }
  | { kind: "downgrade"; model: string }
  | { kind: "refuse"; message: string };

/**
 * core/ai.py guard(), the same thresholds: under 80% as asked; from 80% a
 * non-critical call uses the cheapest model; at or above 100% a non-critical
 * call is refused. A critical call is never downgraded or refused.
 */
export function decide(spent: number, budget: number, model: string, critical: boolean): BudgetDecision {
  if (critical) return { kind: "normal", model };
  if (spent >= budget) {
    return {
      kind: "refuse",
      message: `This month's AI budget is spent ($${spent.toFixed(2)} of $${budget.toFixed(2)}). Wags is back on the 1st, or when the budget is raised.`,
    };
  }
  if (spent >= budget * DOWNGRADE_AT && model !== CHEAPEST_MODEL) return { kind: "downgrade", model: CHEAPEST_MODEL };
  return { kind: "normal", model };
}

/** Which once-a-month alert this spend level owes, if any (the worker sends the same kinds). */
export function alertDue(
  spent: number,
  budget: number,
  sent: { alerted_80: boolean; alerted_100: boolean },
): "ai_budget_80" | "ai_budget_100" | null {
  if (spent >= budget) return sent.alerted_100 ? null : "ai_budget_100";
  if (spent >= budget * DOWNGRADE_AT) return sent.alerted_80 ? null : "ai_budget_80";
  return null;
}

/**
 * The mock model is used only off Vercel and only when asked: VERCEL unset
 * AND WAGS_MOCK=1, the e2e marker's rule. Every Vercel deployment, preview
 * included, always talks to the real model.
 */
export function mockAllowed(env: Record<string, string | undefined>): boolean {
  return !env.VERCEL && env.WAGS_MOCK === "1";
}
