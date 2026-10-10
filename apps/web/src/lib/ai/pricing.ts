// The web twin of workers/core/ai.py PRICING: USD per million tokens,
// Anthropic first-party rates verified 2026-10-10 against
// https://platform.claude.com/docs/en/about-claude/pricing.
//
// Kept as plain literals on purpose: workers/tests/test_ai.py
// (WebTwinPricing) parses this file and fails if any rate here differs from
// core/ai.py, so the web and the worker can never cost a call differently.
// Change both together.

export type Rates = {
  input: number;
  output: number;
  cacheWrite5m: number;
  cacheWrite1h: number;
  cacheRead: number;
};

export type Price = {
  standard: Rates;
  /** Applies when the prompt (input + cache reads + writes) exceeds longPromptOver. */
  longPrompt?: Rates;
  longPromptOver?: number;
};

export const PRICING: Record<string, Price> = {
  "claude-fable-5-1": {
    standard: { input: 10, output: 50, cacheWrite5m: 12.5, cacheWrite1h: 20, cacheRead: 0.25 },
  },
  "claude-opus-5-5": {
    standard: { input: 4, output: 20, cacheWrite5m: 5, cacheWrite1h: 8, cacheRead: 0.2 },
  },
  "claude-sonnet-5-5": {
    standard: { input: 2, output: 10, cacheWrite5m: 2.5, cacheWrite1h: 4, cacheRead: 0.1 },
  },
  "claude-haiku-5-5": {
    standard: { input: 0.1, output: 0.5, cacheWrite5m: 0.125, cacheWrite1h: 0.2, cacheRead: 0.01 },
    longPrompt: { input: 0.5, output: 2.5, cacheWrite5m: 0.625, cacheWrite1h: 1, cacheRead: 0.05 },
    longPromptOver: 100_000,
  },
};

export const WAGS_MODEL = "claude-sonnet-5-5";
/** core/ai.py CHEAPEST_MODEL: where non-critical calls go past 80% of the budget. */
export const CHEAPEST_MODEL = "claude-haiku-5-5";

export type Usage = {
  tokensIn: number;
  tokensOut: number;
  cacheRead: number;
  /** Every Wags write uses the default 5-minute TTL. */
  cacheWrite5m: number;
  cacheWrite1h?: number;
};

/**
 * Exact cost of one call in USD, rounded half-up to the micro-dollar like
 * core/ai.py cost_usd. Computed in integer micro-units of the per-million
 * rate, so no float drift. An unpriced model throws: a cost at a guessed
 * rate would corrupt the budget.
 */
export function costUsd(model: string, u: Usage): number {
  const price = PRICING[model];
  if (!price) throw new Error(`No pricing for model ${model}; add it to src/lib/ai/pricing.ts and core/ai.py.`);
  const prompt = u.tokensIn + u.cacheRead + u.cacheWrite5m + (u.cacheWrite1h ?? 0);
  const r = price.longPrompt && prompt > (price.longPromptOver ?? Infinity) ? price.longPrompt : price.standard;
  // Rates have at most 3 decimals: scale to thousandths of a dollar per million.
  const k = (x: number) => Math.round(x * 1000);
  const milliDollarTokens =
    u.tokensIn * k(r.input) +
    u.tokensOut * k(r.output) +
    u.cacheWrite5m * k(r.cacheWrite5m) +
    (u.cacheWrite1h ?? 0) * k(r.cacheWrite1h) +
    u.cacheRead * k(r.cacheRead);
  // tokens x ($ per million) is exactly micro-dollars; rates were scaled by
  // 1000, so divide by 1000 rounding half-up (cost is never negative).
  const micro = Math.floor((milliDollarTokens + 500) / 1000);
  return micro / 1e6;
}
