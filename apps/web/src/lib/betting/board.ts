// The Kalshi board's pure logic (Chat 8 Phase 1; db/071; docs/kalshi_board.md).
// No page, no server: the shapes db/071's readers return, the URL filters,
// and the formatting. Prices are Kalshi's YES side in dollars (= probability).

export type Kind = "game_line" | "prop" | "other";

/** kalshi_board_sports: one chip per sport. */
export type SportChip = { sport: string; games: number; markets: number; lines?: number; props?: number; combo?: number };

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
  c?: number; // combo-eligible markets
  asof: string; // the event's last fetch
  test?: boolean;
};

/** kalshi_board_game: one event of an expanded game, with its markets. */
export type BoardMarket = { k: Kind; lb?: string; b?: number; a?: number; f?: number; v?: string };
export type BoardEvent = { e: string; sr: string; c?: boolean; y?: boolean; asof: string; m: BoardMarket[] };

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

/** "12 lines · 340 props · 6 other". */
export function countsLine(g: Pick<BoardGame, "l" | "p" | "o">): string {
  const parts = [
    g.l ? `${g.l} ${g.l === 1 ? "line" : "lines"}` : null,
    g.p ? `${g.p} ${g.p === 1 ? "prop" : "props"}` : null,
    g.o ? `${g.o} other` : null,
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
