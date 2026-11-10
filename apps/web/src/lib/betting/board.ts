// The Kalshi board's pure logic (Chat 8 Phase 1; db/071; docs/kalshi_board.md).
// No page, no server: the shapes db/071's readers return, the URL filters,
// and the formatting. Prices are Kalshi's YES side in dollars (= probability).

export type Kind = "game_line" | "prop" | "other";

/** kalshi_board_sports: one chip per sport. lines, props and combo count
 *  binary markets only; scalar (ladders, escalators) apart (db/072). */
export type SportChip = {
  sport: string; games: number; markets: number; lines?: number; props?: number; scalar?: number; combo?: number;
};

/** kalshi_board_games: one collapsed game. */
export type BoardGame = {
  g: string; // game ticker
  t: string; // title
  at: string; // starts_at
  lg?: string; // our league code, when there is one
  se?: number; // sports_events id, when linked
  l?: number; // game-line markets
  p?: number; // props
  o?: number; // other
  s?: number; // scalar markets (ladders, escalators; db/072): never legs, counted apart
  c?: number; // combo-eligible binary markets
  asof: string; // the event's last fetch
  test?: boolean;
};

/** kalshi_board_game: one event of an expanded game, with its markets.
 *  s: a scalar market (a ladder or escalator: YES pays a fraction of $1 that
 *  grows with the stat; its price is not a probability, never a parlay leg). */
export type BoardMarket = {
  k: Kind; lb?: string; b?: number; a?: number; f?: number; v?: string; s?: boolean;
  /** The packed consensus (db/074): "<tier><letter><p><edge>", e.g. "b9c515-93". */
  q?: string;
};

/** A binary market's consensus (db/074 leg_consensus_for, packed on
 *  kalshi_board_game; parseConsensus). t: tier b(ooks) | e(spn) | k(alshi
 *  only); n: books at the exact line; p: P(YES); sd: the better side; e: its
 *  net edge (p - ask - taker fee); a, f: its ask and fee (YES: the market's
 *  own; NO: 1 - bid and the fee derived to 0.1 cent, fApprox); ec: the edge
 *  is claimed (books or espn, edge > 0, both prices fresh); staleK: the
 *  Kalshi price is older than 20 min; staleO: the book price older than 12 h.
 *  A kalshi-tier or stale leg never claims edge (owner, Nov 9). */
export type Consensus = {
  t: "b" | "e" | "k"; n?: number; p: number; sd: "y" | "n"; e: number; a?: number; f?: number;
  fApprox?: boolean; ec: boolean; staleK: boolean; staleO: boolean;
};

const PACKED = /^([bek])(\d*)([a-p])(\d{3})(-?\d+)$/;

/** The packed q into its fields, with the side's ask and fee from the market. */
export function parseConsensus(m: Pick<BoardMarket, "q" | "a" | "b" | "f">): Consensus | null {
  const hit = m.q ? PACKED.exec(m.q) : null;
  if (!hit) return null;
  const code = hit[3].charCodeAt(0) - 97;
  const p = Number(hit[4]) / 1000;
  const e = Number(hit[5]) / 1000;
  const sd = code & 8 ? "n" : "y";
  const t = hit[1] as Consensus["t"];
  const base = { t, n: t === "b" ? Number(hit[2]) : t === "e" ? 1 : undefined, p, sd, e,
                 ec: (code & 1) === 1, staleK: (code & 2) === 2, staleO: (code & 4) === 4 } as const;
  if (sd === "y") return { ...base, a: m.a, f: m.f };
  const a = m.b === undefined || m.b === null ? undefined : Math.round((1 - m.b) * 10000) / 10000;
  const f = a === undefined ? undefined : Math.round(((1 - p) - a - e) * 1000) / 1000;
  return { ...base, a, f: f !== undefined && f >= 0 ? f : undefined, fApprox: true };
}

/** "stale: Kalshi price" / "stale: book price" / both, or null. */
export function staleLabel(q: Pick<Consensus, "staleK" | "staleO">): string | null {
  if (q.staleK && q.staleO) return "stale prices";
  if (q.staleK) return "stale Kalshi price";
  if (q.staleO) return "stale book price";
  return null;
}

/** "Books · 9", "ESPN · 1 book", "Kalshi only". */
export function tierLabel(q: Pick<Consensus, "t" | "n">): string {
  if (q.t === "b") return `Books · ${q.n ?? 0}`;
  if (q.t === "e") return "ESPN · 1 book";
  return "Kalshi only";
}

/** A probability as a percent with one decimal: 0.5153 -> "51.5%". */
export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(Math.round(v * 1000) / 10).toFixed(1)}%`;
}

/** A net edge in cents, signed: 0.04796 -> "+4.8¢", -0.0925 -> "−9.3¢". */
export function edgeCents(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const c = Math.round(v * 1000) / 10;
  if (c === 0) return "0.0¢";
  return `${c > 0 ? "+" : "−"}${Math.abs(c).toFixed(1)}¢`;
}

/** up only for a claimed, positive edge; anything else is neutral (never
 *  the danger colour: an edge at or under zero is not a loss). */
export function edgeTone(q: Pick<Consensus, "e" | "ec">): "up" | "flat" {
  return q.ec && (q.e ?? 0) > 0 ? "up" : "flat";
}

/** The Odds API's month (db/073 odds_api_usage). */
export type Credits = { used: number | null; spent: number; allowance: number; remaining: number | null; calls: number };

/** The account's own count (x-requests-used, db/074) when known, else the log's sum. */
export function creditsLine(c: Credits): string {
  return `Odds API credits: ${c.used ?? c.spent} / ${c.allowance} used this month`
    + (c.remaining !== null && c.remaining !== undefined ? ` · ${c.remaining} left on the account` : "");
}
/** sm: how many of this event's markets one Kalshi combo may use (absent =
 *  no stated per-event cap; never read as 0 or 1). c, y and sm come from the
 *  event's binary markets only (db/072): an all-scalar event has no combo chip. */
export type BoardEvent = { e: string; sr: string; c?: boolean; y?: boolean; sm?: number; asof: string; m: BoardMarket[] };

/** The combo chip's words: "Combo", "Combo · YES only", "... · max 1". */
export function comboLabel(ev: Pick<BoardEvent, "y" | "sm">): string {
  return ["Combo", ev.y ? "YES only" : null, ev.sm !== undefined && ev.sm !== null ? `max ${ev.sm}` : null]
    .filter(Boolean).join(" · ");
}

export type Filters = { sport: string | null; kind: Kind | null; combo: boolean };

const KINDS: readonly Kind[] = ["game_line", "prop", "other"];

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** The URL's filters (?sport=&kind=lines|props&combo=1), cleaned. */
export function parseFilters(params: { sport?: string | string[]; kind?: string | string[]; combo?: string | string[] }): Filters {
  const sport = first(params.sport)?.trim().slice(0, 40) || null;
  const k = first(params.kind);
  const kind: Kind | null = k === "lines" ? "game_line" : k === "props" ? "prop" : k === "other" ? "other" : null;
  return { sport, kind, combo: first(params.combo) === "1" };
}

export function isKind(v: unknown): v is Kind {
  return typeof v === "string" && (KINDS as readonly string[]).includes(v);
}

/** The board's URL for a set of filters (the chips are links). */
export function boardHref(f: Filters): string {
  const q = new URLSearchParams();
  if (f.sport) q.set("sport", f.sport);
  if (f.kind) q.set("kind", f.kind === "game_line" ? "lines" : f.kind === "prop" ? "props" : "other");
  if (f.combo) q.set("combo", "1");
  const s = q.toString();
  return s ? `/betting?${s}` : "/betting";
}

/** The sport shown: the URL's when it is on the board, else the busiest. */
export function pickSport(chips: readonly SportChip[], wanted: string | null): string | null {
  if (wanted && chips.some((c) => c.sport === wanted)) return wanted;
  return chips[0]?.sport ?? null;
}

/** A price in cents: 0.61 -> "61¢", 0.015 -> "1.5¢", none -> "—". */
export function cents(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const c = Math.round(v * 1000) / 10;
  return `${Number.isInteger(c) ? c.toFixed(0) : c.toFixed(1)}¢`;
}

/** The taker fee per contract at the ask: 0.016317 -> "1.63¢". */
export function feeCents(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(Math.round(v * 10000) / 100).toFixed(2)}¢`;
}

const CHICAGO = "America/Chicago";

/** "Sat 6:30 PM" in the owner's time. */
export function startLabel(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-US", { weekday: "short", timeZone: CHICAGO });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: CHICAGO });
  return `${day} ${time}`;
}

/** "as of 3:30 PM" (the source time: the event's last fetch). */
export function asOfLabel(iso: string): string {
  return `as of ${new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: CHICAGO })}`;
}

/** "12 lines · 340 props · 6 other · 20 scalar" (scalar counted apart). */
export function countsLine(g: Pick<BoardGame, "l" | "p" | "o" | "s">): string {
  const parts = [
    g.l ? `${g.l} ${g.l === 1 ? "line" : "lines"}` : null,
    g.p ? `${g.p} ${g.p === 1 ? "prop" : "props"}` : null,
    g.o ? `${g.o} other` : null,
    g.s ? `${g.s} scalar` : null,
  ].filter(Boolean);
  return parts.join(" · ") || "No markets";
}

/** An expanded game's events in the board's order: game lines first, then
 *  props, then other (the server orders them; this keeps a stable sort). */
export function orderEvents(events: readonly BoardEvent[]): BoardEvent[] {
  const rank = (e: BoardEvent) => Math.min(...e.m.map((m) => KINDS.indexOf(m.k)), 3);
  return [...events].sort((a, b) => rank(a) - rank(b) || a.sr.localeCompare(b.sr) || a.e.localeCompare(b.e));
}

export const KIND_LABEL: Record<Kind, string> = { game_line: "Lines", prop: "Props", other: "Other" };
