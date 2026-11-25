// "Bet $X to win $Y" (E1; owner, Nov 25; docs/payout_target.md): the request's
// numbers, the worker's answer (db/094's payout_answers) and its words. No page,
// no server. A suggestion, never a bet (CLAUDE.md §1).

import type { ComboLeg } from "./combo";
import { toLeg, type SingleRow } from "./singles";

/** One leg as consensus/payout.py ticket_json writes it. */
export type PayoutLeg = {
  m: string; side: "yes" | "no"; k: string; label?: string; who?: string; stat?: string; line?: number;
  team?: string; per?: string; game?: string; sport?: string; start?: number; ask: number; p: number;
  tier: "books" | "espn" | "kalshi"; ya?: number; na?: number; yo?: boolean; age?: number;
};

export type PayoutTicket = {
  k: number;
  /** The combo's price before the fee (the product of the legs' asks), and the fee per contract. */
  price: number; fee: number;
  /** Whole contracts the stake buys: the dollars paid back if every leg hits. */
  contracts: number;
  /** The correlated fair chance every leg hits, its Monte Carlo SE, and the plain product. */
  fair: number; se?: number | null; naive: number;
  /** Expected return on the stake after the fee: (fair × contracts − stake) / stake. */
  exp: number;
  combo: "fits" | "build_manually"; combo_reason?: string | null;
  pairs: number; measured: number; prior: number;
  legs: PayoutLeg[];
};

export type PayoutAnswer = {
  tickets: PayoutTicket[]; nearest: boolean; target_price: number; candidates: number; compute_ms: number;
};

export type PayoutState =
  | { status: "queued"; createdAt: string }
  | { status: "answered"; createdAt: string; answeredAt: string; answer: PayoutAnswer }
  | { status: "failed"; reason: string; createdAt: string };

/** db/094's limits: stake $1 to $10,000; the payout above the stake, at most $1,000,000. */
export const STAKE_MIN = 1;
export const STAKE_MAX = 10_000;
export const PAYOUT_MAX = 1_000_000;
/** Past this multiple the page warns: the longshot corner (owner, Nov 25). */
export const WARN_MULTIPLE = 100;

/** The two amounts as dollars to the cent, or the reason they can't be asked. */
export function parseAmounts(stake: unknown, payout: unknown): { stake: number; payout: number } | { error: string } {
  const s = Math.round(Number(stake) * 100) / 100;
  const p = Math.round(Number(payout) * 100) / 100;
  if (!Number.isFinite(s) || s < STAKE_MIN || s > STAKE_MAX) return { error: `Stake $${STAKE_MIN} to $${STAKE_MAX.toLocaleString("en-US")}.` };
  if (!Number.isFinite(p) || p > PAYOUT_MAX) return { error: `Pays back at most $${PAYOUT_MAX.toLocaleString("en-US")}.` };
  if (p <= s) return { error: "Pays back has to be more than the stake." };
  return { stake: s, payout: p };
}

export const warns = (stake: number, payout: number) => payout / stake > WARN_MULTIPLE;

/** "about 1 in 11" (and "about 3 in 4" above even). */
export function chanceWords(p: number): string {
  if (!(p > 0)) return "no chance on these prices";
  if (p >= 0.5) {
    const pct = Math.round(p * 100);
    return `about ${pct} in 100`;
  }
  return `about 1 in ${Math.round(1 / p)}`;
}

/** "−9.8%": the expected return on the stake, after the fee. */
export function expText(x: number): string {
  const v = Math.round(x * 1000) / 10;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

export const dollars = (x: number) => `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

/** A ticket's legs as slip legs: the Props tab's words and the slip's both-sides prices. */
export function ticketLegs(t: PayoutTicket): ComboLeg[] {
  return t.legs.map((l) => {
    const row: SingleRow = {
      m: l.m, side: l.side, k: l.k, label: l.label, who: l.who, stat: l.stat, line: l.line, team: l.team,
      per: l.per, game: l.game, start: l.start, ask: l.ask, fee: 0, p: l.p, mult: 0, tier: l.tier,
      ya: l.ya, na: l.na, age: l.age, combo: l.yo ? "yes_only" : undefined,
    };
    return toLeg(row, l.sport);
  });
}
