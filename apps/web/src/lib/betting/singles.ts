// The Props tab's pure logic (owner, Nov 25; docs/props_tab.md): db/093's rows,
// the filters in the URL, a row in words, and a row as a slip leg. No page, no
// server. Nothing here bets (CLAUDE.md §1).

import type { ComboLeg } from "./combo";
import { legWords, type LegWords } from "./parlays";
import { multiplier, percent, type PriceUnit } from "./price";

/** One side of one market, as kalshi_singles returns it (db/093). */
export type SingleRow = {
  m: string;
  side: "yes" | "no";
  k: string; // prop | game_line | other
  label?: string;
  who?: string;
  stat?: string;
  line?: number;
  team?: string;
  per?: string;
  game?: string;
  gt?: string;
  g?: number;
  start?: number;
  /** This side's ask and Kalshi's taker fee on it. */
  ask: number;
  fee: number;
  /** Fair chance of this side, and its edge after the fee (p − ask − fee). */
  p?: number;
  edge?: number;
  /** What $1 returns if it hits, after the fee: 1 / (ask + fee). */
  mult: number;
  /** Where p comes from: books, espn, or kalshi (Kalshi's own mid: no outside view). */
  tier: "books" | "espn" | "kalshi";
  /** Both sides' asks, for the slip's flip. */
  ya?: number;
  na?: number;
  /** Seconds since Kalshi's price was read. */
  age?: number;
  combo?: "no" | "yes_only";
};

export type SinglesPage = { asof: number; counts: Partial<Record<SingleTier, number>>; rows: SingleRow[] };

export const SINGLE_TIERS = ["safer", "mid", "lotto"] as const;
export type SingleTier = (typeof SINGLE_TIERS)[number] | "low";
export const SINGLE_TIER_NAME: Record<SingleTier, string> = { safer: "Safer", mid: "Mid", lotto: "Lotto", low: "Under 5¢" };
export const SINGLE_TIER_BAND: Record<SingleTier, string> = {
  safer: "60–85¢", mid: "30–60¢", lotto: "5–30¢", low: "under 5¢",
};

export type SinglesKind = "prop" | "line";
export type SinglesSort = "edge" | "mult";
export const PAGE_SIZE = 50;
/** Kalshi's price older than this is stale (db/074's 20 minutes). */
export const STALE_S = 20 * 60;

export type SinglesQuery = {
  sport?: string;
  kind: SinglesKind;
  tier: SingleTier;
  q?: string;
  sort: SinglesSort;
  offset: number;
};

type Raw = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** The URL's filters, each falling back to its default: Props, Safer, by edge, the first page. */
export function parseSingles(raw: Raw): SinglesQuery {
  const tier = one(raw.tier);
  const q = one(raw.q)?.trim().slice(0, 60);
  const offset = Number(one(raw.offset));
  return {
    sport: one(raw.sport)?.trim().slice(0, 40) || undefined,
    kind: one(raw.kind) === "line" ? "line" : "prop",
    tier: tier === "mid" || tier === "lotto" || tier === "low" ? tier : "safer",
    q: q || undefined,
    sort: one(raw.sort) === "mult" ? "mult" : "edge",
    offset: Number.isInteger(offset) && offset > 0 ? Math.min(offset, 100_000) : 0,
  };
}

/** The page's URL for a query (defaults left out). A change of filter starts at the first page. */
export function singlesHref(q: SinglesQuery, change: Partial<SinglesQuery> = {}): string {
  const next = { ...q, offset: "offset" in change ? change.offset! : 0, ...change };
  const s = new URLSearchParams();
  if (next.sport) s.set("sport", next.sport);
  if (next.kind !== "prop") s.set("kind", next.kind);
  if (next.tier !== "safer") s.set("tier", next.tier);
  if (next.q) s.set("q", next.q);
  if (next.sort !== "edge") s.set("sort", next.sort);
  if (next.offset > 0) s.set("offset", String(next.offset));
  const t = s.toString();
  return `/betting/props${t ? `?${t}` : ""}`;
}

/** The row in words: the shared phrasebook (the Parlays page's), so a prop reads the same everywhere. */
export function rowWords(r: SingleRow): LegWords {
  return legWords({
    side: r.side, label: r.label ?? r.m, kind: r.k, who: r.who, stat: r.stat, line: r.line,
    team: r.team, per: r.per, gt: r.game,
  });
}

/** The price after the fee, in the chosen unit: × is what $1 returns, % what one $1 contract costs. */
export function costText(r: Pick<SingleRow, "ask" | "fee">, unit: PriceUnit): string {
  const cost = r.ask + r.fee;
  return unit === "x" ? multiplier(1 / cost) : percent(cost);
}

/** In cents, one decimal, signed: 0.0213 -> "+2.1¢", -0.019 -> "−1.9¢". */
export function cents(x: number): string {
  const v = Math.round(x * 1000) / 10;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}¢`;
}

/**
 * The edge, worded for what it is. With an outside line (books, ESPN), the edge after the fee. On Kalshi's
 * own price there is no outside view, so the same number is only what it costs to trade: never a claim.
 */
export function edgeText(r: Pick<SingleRow, "edge" | "tier">): string | undefined {
  if (r.edge === undefined) return undefined;
  if (r.tier === "kalshi") return r.edge < 0 ? `costs ${cents(-r.edge).slice(1)} to trade` : "at Kalshi's own price";
  return `${cents(r.edge)} vs ${r.tier === "books" ? "books" : "ESPN"}`;
}

export function ageText(s: number | undefined): string | undefined {
  if (s === undefined) return undefined;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min old`;
  return `${Math.floor(s / 3600)} h old`;
}

export const stale = (r: Pick<SingleRow, "age">) => (r.age ?? 0) > STALE_S;

/** The row as a slip leg: its side at its ask, both sides kept for a flip, YES-only kept. */
export function toLeg(r: SingleRow, sport: string | undefined, words = rowWords(r)): ComboLeg {
  const label = words.detail && words.game ? `${words.title}: ${words.detail}` : words.title;
  const leg: ComboLeg = { t: r.m, side: r.side, label, game: words.game ?? words.detail ?? r.game ?? "", a: r.ask };
  if (sport) leg.sport = sport;
  if (r.team) leg.tm = r.team;
  if (r.ya !== undefined && r.ya > 0 && r.ya < 1) leg.py = r.ya;
  if (r.na !== undefined && r.na > 0 && r.na < 1) leg.pn = Math.round(r.na * 10000) / 10000;
  if (r.combo === "yes_only") leg.yo = true;
  return leg;
}

/** "Tonight 7:30 PM" / "Sat 1:00 PM", in Chicago, as the Board writes a start. */
export function startText(epoch: number | undefined, now = new Date()): string | undefined {
  if (!epoch) return undefined;
  const d = new Date(epoch * 1000);
  const day = (x: Date) => x.toLocaleDateString("en-US", { timeZone: "America/Chicago" });
  const time = d.toLocaleTimeString("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" });
  if (day(d) === day(now)) return `Today ${time}`;
  return `${d.toLocaleDateString("en-US", { timeZone: "America/Chicago", weekday: "short" })} ${time}`;
}
