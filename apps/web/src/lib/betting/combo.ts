// Combo mode's pure logic (Chat 8 Phase 3; db/076, db/077; docs/correlation.md).
// No page, no server: the ticked legs (kept per viewer in localStorage, so a
// tick survives switching games and sports), the request's shape, the quote
// db/076 stores, and the sheet's words. Nothing here bets (CLAUDE.md §1).

import type { BoardEvent, BoardMarket } from "./board";

export type Side = "yes" | "no";

/** One ticked leg: enough to show it and to send it. */
export type ComboLeg = {
  t: string; // market ticker
  side: Side;
  label: string;
  game: string; // the game's title
  sport?: string;
};

export const MAX_LEGS = 25;
export const MIN_LEGS = 2;
const STORAGE_KEY = "vm-combo-legs";
const TICKER = /^[A-Z0-9][A-Z0-9._-]{0,99}$/;

/** A market's ticker from its event and db/077's suffix `x` ("" = the event's own). */
export function marketTicker(ev: Pick<BoardEvent, "e">, m: Pick<BoardMarket, "x">): string | null {
  if (m.x === undefined || m.x === null) return null;
  return m.x === "" ? ev.e : `${ev.e}-${m.x}`;
}

/** Tick a side; ticking the side already ticked unticks it; the other side replaces it. */
export function toggleLeg(legs: readonly ComboLeg[], leg: ComboLeg): ComboLeg[] {
  const i = legs.findIndex((l) => l.t === leg.t);
  if (i >= 0 && legs[i].side === leg.side) return legs.filter((_, j) => j !== i);
  if (i >= 0) return legs.map((l, j) => (j === i ? leg : l));
  if (legs.length >= MAX_LEGS) return [...legs];
  return [...legs, leg];
}

export function sideOf(legs: readonly ComboLeg[], ticker: string | null): Side | null {
  if (!ticker) return null;
  return legs.find((l) => l.t === ticker)?.side ?? null;
}

function isLeg(v: unknown): v is ComboLeg {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return typeof o.t === "string" && TICKER.test(o.t) && (o.side === "yes" || o.side === "no")
    && typeof o.label === "string" && typeof o.game === "string";
}

/** The legs kept in this browser; [] when storage is empty, blocked or off. */
export function loadLegs(storage: Pick<Storage, "getItem"> | null | undefined): ComboLeg[] {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return [];
    const v: unknown = JSON.parse(raw);
    return Array.isArray(v) ? v.filter(isLeg).slice(0, MAX_LEGS) : [];
  } catch {
    return [];
  }
}

export function saveLegs(storage: Pick<Storage, "setItem" | "removeItem"> | null | undefined, legs: readonly ComboLeg[]) {
  try {
    if (!storage) return;
    if (legs.length === 0) storage.removeItem(STORAGE_KEY);
    else storage.setItem(STORAGE_KEY, JSON.stringify(legs));
  } catch {
    // A private window or blocked storage: the ticks live for this page only.
  }
}

/** The request db/076 takes: [{market_ticker, side}], validated here too. */
export function requestLegs(legs: readonly unknown[]): { market_ticker: string; side: Side }[] | null {
  if (!Array.isArray(legs) || legs.length < MIN_LEGS || legs.length > MAX_LEGS) return null;
  const out: { market_ticker: string; side: Side }[] = [];
  for (const l of legs) {
    if (!l || typeof l !== "object") return null;
    const o = l as Record<string, unknown>;
    if (typeof o.t !== "string" || !TICKER.test(o.t) || (o.side !== "yes" && o.side !== "no")) return null;
    out.push({ market_ticker: o.t, side: o.side });
  }
  return out;
}

/** db/076's combo_quotes row, as the sheet reads it. */
export type QuoteLeg = { t: string; side: Side; label?: string; p: number; tier: string; stale?: boolean; cost?: number | null };
export type QuoteBlock = {
  game: string; p: number; se: number | null; method: "exact" | "bivariate" | "monte_carlo"; draws: number;
  shrink: number; latents: number; nested: string[][];
  pairs: { a: number; b: number; rho: number; cls: string; key: string | null }[]; legs: QuoteLeg[];
};
export type Quote = {
  naive_p: number; fair_p: number; se: number | null; blocks: QuoteBlock[]; kalshi_naive_cost: number | null;
  combo_status: "fits" | "build_manually"; combo_reason: string | null; prior_pairs: number;
  unmodeled_pairs: number; rho_version: string; compute_ms: number;
};
export type ComboState =
  | { status: "queued"; createdAt: string }
  | { status: "answered"; quote: Quote; createdAt: string; answeredAt: string }
  | { status: "refused" | "failed"; reason: string; createdAt: string };

/** "38.4%" with one decimal; small values keep two significant figures. */
export function probLabel(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  if (p > 0 && p < 0.001) return `${(p * 100).toPrecision(2)}%`;
  return `${(Math.round(p * 1000) / 10).toFixed(1)}%`;
}

/** "± 0.21%" for a Monte Carlo SE; "" when exact. */
export function seLabel(se: number | null | undefined): string {
  if (se === null || se === undefined) return "";
  return `± ${se < 0.0001 ? (se * 100).toPrecision(2) : (Math.round(se * 10000) / 100).toFixed(2)}%`;
}

/** fair / naive: "×1.18". */
export function ratioLabel(q: Pick<Quote, "fair_p" | "naive_p">): string {
  if (!q.naive_p) return "—";
  return `×${(q.fair_p / q.naive_p).toFixed(2)}`;
}

/** "Kalshi combo · fits" or "Build manually: <why>". */
export function comboStatusLabel(q: Pick<Quote, "combo_status" | "combo_reason">): string {
  return q.combo_status === "fits" ? "Kalshi combo · fits" : `Build manually: ${q.combo_reason ?? "Kalshi can't take it as one combo"}`;
}

/** A dollar cost per $1 contract: 0.2345 -> "$0.23". */
export function costLabel(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `$${v.toFixed(2)}`;
}

/** "Pricing… 7 s". */
export function pricingLabel(seconds: number): string {
  return `Pricing… ${Math.max(0, Math.floor(seconds))} s`;
}

/** The refusal's words without the table's prefix ("combo_requests: ..."). */
export function cleanReason(reason: string): string {
  const r = reason.replace(/^combo_requests:\s*/, "").trim();
  return r.charAt(0).toUpperCase() + r.slice(1);
}

/** The leg's quoted p and tier, by ticker, from the quote's blocks. */
export function quotedLegs(q: { blocks: readonly { legs: readonly QuoteLeg[] }[] }): Map<string, QuoteLeg> {
  const out = new Map<string, QuoteLeg>();
  for (const b of q.blocks) for (const l of b.legs) out.set(l.t, l);
  return out;
}

/** "Books", "ESPN", "Kalshi only" for a quoted leg's tier. */
export function legTierLabel(tier: string): string {
  return tier === "books" ? "Books" : tier === "espn" ? "ESPN" : "Kalshi only";
}
