// The game page's pure logic (Chat 8 Phase 3.5, Workstream B; db/079). No
// page, no server: the shapes kalshi_board_game_tabs and kalshi_board_game_tab
// return, and how a tab's events become boxes, ladders, tiles and player rows.
//
// Real boards are irregular (one NFL game on Nov 15: 22 line series, 49 player
// stats, five "race to" events in one series), so nothing here names a series:
// the grouping reads each market's own fields.

import { parseConsensus } from "./board";

export type Tab = "lines" | "players" | "periods";
export const TABS: readonly Tab[] = ["lines", "players", "periods"];
export const TAB_LABEL: Record<Tab, string> = { lines: "Game lines", players: "Players", periods: "Halves and quarters" };

export function isTab(v: unknown): v is Tab {
  return typeof v === "string" && (TABS as readonly string[]).includes(v);
}

/** kalshi_board_game_tabs: the three tabs' market counts and the Players tab's stats. */
export type GameTabs = {
  lines: number; players: number; periods: number;
  /** Players markets with no stat: the "Other" chip, shown only when > 0. */
  other: number;
  stats: { st: string; n: number }[];
  asof: string | null;
};

/** One market of kalshi_board_game_tab. x: the ticker after the event's ("" = the event's own). */
export type TabMarket = {
  x: string; sj?: string; ln?: number; st?: string; tm?: string; lb?: string;
  b?: number; a?: number; f?: number; v?: string; s?: boolean; q?: string;
};

/** One event of kalshi_board_game_tab: series, its title, the period, combo flags, source time. */
export type TabEvent = {
  e: string; sr: string; t?: string; pd?: string; c?: boolean; y?: boolean; sm?: number; asof: string;
  m: TabMarket[];
};

/** The game, from its events (the page's header). */
export type GameMeta = {
  g: string; title: string; startsAt: string | null; sport: string | null; league: string | null; test: boolean;
  /** The linked ESPN game's teams, db/083's card shape: [city, nickname, logo key, colour], away first unless hs. */
  tm?: [string, string | null, string | null, string | null][]; hs?: boolean;
};

/** "Other" is asked for as the empty stat (db/079). */
export const OTHER_STAT = "";

export function marketTicker(ev: Pick<TabEvent, "e">, m: Pick<TabMarket, "x">): string {
  return m.x === "" ? ev.e : `${ev.e}-${m.x}`;
}

/** What a market says, in words: its label, else its subject. */
export function marketLabel(m: Pick<TabMarket, "lb" | "sj" | "x">): string {
  return m.lb ?? m.sj ?? m.x;
}

/** The moneyline event: every market a "winner" with no line. It rides in the game's header. */
export function isWinnerEvent(ev: TabEvent): boolean {
  return ev.m.length > 0 && ev.m.every((m) => m.st === "winner" && (m.ln === undefined || m.ln === null));
}

/** "A at B" / "A vs B": the two sides as written, each with its code (the first word). */
export function sidesOf(title: string): { a: Side; h: Side } | null {
  const hit = /^(.+?)\s+(?:at|vs\.?|@)\s+(.+)$/i.exec(title.trim());
  if (!hit) return null;
  const side = (s: string): Side => {
    const parts = s.trim().split(/\s+/);
    return { code: parts[0], name: parts.length > 1 ? parts.slice(1).join(" ") : parts[0] };
  };
  return { a: side(hit[1]), h: side(hit[2]) };
}
export type Side = { code: string; name: string };

/** A box's title: the series title without the league's prefix, plus what tells two events of one
 *  series apart (eventTag), when the tab holds more than one event of that series. */
export function boxTitle(ev: Pick<TabEvent, "e" | "t" | "sr">, game: string, repeated: boolean): string {
  const base = (ev.t ?? ev.sr).replace(/^(Pro Football|Pro Basketball|Pro Hockey|College Football|College Basketball|NFL|NBA|NHL)\s+/, "");
  const tag = repeated ? eventTag(ev.e, game) : null;
  return tag ? `${base} · ${tag}` : base;
}

/** The event's own words after the game's code: KXNFLRACE-26NOV15SFDAL20 in game ...-26NOV15SFDAL -> "20". */
export function eventTag(e: string, game: string): string | null {
  const g = game.split("-").slice(1).join("-");
  const tail = e.split("-").slice(1).join("-");
  if (!g || !tail.startsWith(g)) return null;
  const rest = tail.slice(g.length).replace(/^-/, "");
  return rest || null;
}

/** "1st half", "3rd quarter", "2nd period". */
export function periodLabel(pd: string): string {
  const n = Number(pd[0]);
  const nth = n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`;
  const unit = pd[1] === "H" ? "half" : pd[1] === "Q" ? "quarter" : "period";
  return `${nth} ${unit}`;
}

const PERIOD_ORDER = ["1H", "2H", "1Q", "2Q", "3Q", "4Q", "1P", "2P", "3P"];

/** The periods a tab's events carry, in game order. */
export function periodsOf(events: readonly Pick<TabEvent, "pd">[]): string[] {
  const have = new Set(events.map((e) => e.pd).filter((p): p is string => !!p));
  return [...have].sort((x, y) => PERIOD_ORDER.indexOf(x) - PERIOD_ORDER.indexOf(y));
}

/** A stat's chip: "receiving yards (protected)" -> "Receiving yards · protected"; "" -> "Other". */
export function statLabel(st: string): string {
  if (st === OTHER_STAT) return "Other";
  const s = st.replace(/\s*\(protected\)\s*$/, " · protected");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function num(v: number): string {
  return Number.isInteger(v) ? v.toFixed(0) : String(v);
}

/** A rung's words on a ladder: "25+", "−3.5", "47.5", "2+", or the market's label when it has no line. */
export function rungLabel(m: TabMarket, tab: Tab): string {
  const words = m.lb ?? m.sj ?? "";
  const plus = /(\d+(?:\.\d+)?\+)/.exec(words);
  if (m.ln === undefined || m.ln === null) return plus ? plus[1] : marketLabel(m);
  if (tab === "players") return `${Math.max(1, Math.ceil(m.ln))}+`;
  if (m.st === "spread") return `−${num(m.ln)}`;
  if (m.st === "total" || m.st === "team_total") return num(m.ln);
  if (plus) return plus[1];
  return num(m.ln);
}

/** A ladder: two or more distinct lines. Otherwise the markets are tiles (each its own outcome). */
export function isLadder(ms: readonly TabMarket[]): boolean {
  const lines = new Set(ms.filter((m) => !m.s && typeof m.ln === "number").map((m) => m.ln));
  return lines.size >= 2;
}

/** The books' line where one exists (a books or ESPN consensus), else the rung priced nearest 50¢. */
export function mainIndex(ms: readonly TabMarket[]): number {
  const outside = ms.findIndex((m) => !m.s && /^[be]/.test(m.q ?? ""));
  if (outside >= 0) return outside;
  let best = -1;
  let gap = Infinity;
  ms.forEach((m, i) => {
    if (m.s || typeof m.a !== "number") return;
    const d = Math.abs(m.a - 0.5);
    if (d < gap) {
      gap = d;
      best = i;
    }
  });
  return Math.max(0, best);
}

/** The rungs shown before "All N lines": `size` around the main line (all when there are size + 1 or fewer). */
export function rungWindow(n: number, main: number, size = 9): [number, number] {
  if (n <= size + 1) return [0, n];
  const lo = Math.max(0, Math.min(main - Math.floor(size / 2), n - size));
  return [lo, lo + size];
}

/** Markets split by team, in first-seen order; markets with no team under "". */
export function byTeam<M extends Pick<TabMarket, "tm">>(ms: readonly M[]): Map<string, M[]> {
  const out = new Map<string, M[]>();
  for (const m of ms) {
    const k = m.tm ?? "";
    out.set(k, [...(out.get(k) ?? []), m]);
  }
  return out;
}

/** Sorted by line, then label, for a ladder. */
export function byLine(ms: readonly TabMarket[]): TabMarket[] {
  return [...ms].sort((x, y) => (x.ln ?? -Infinity) - (y.ln ?? -Infinity) || marketLabel(x).localeCompare(marketLabel(y)));
}

/** The order of a tab's boxes: spreads, totals and team totals first (the mock's order), then by title. */
export function orderBoxes(events: readonly TabEvent[]): TabEvent[] {
  const rank = (ev: TabEvent) => {
    const st = ev.m.find((m) => m.st)?.st;
    return st === "spread" ? 0 : st === "total" ? 1 : st === "team_total" ? 2 : 3;
  };
  return [...events].sort((x, y) => rank(x) - rank(y) || (x.t ?? x.sr).localeCompare(y.t ?? y.sr) || x.e.localeCompare(y.e));
}

export type PlayerRow = { name: string; team: string; markets: (TabMarket & { ev: TabEvent })[] };

/** Players by team, each with their markets by line; a player ranked by where the market puts them:
 *  the highest line still priced at 50¢ or more, then the price (the mock's order). */
export function playerRows(events: readonly TabEvent[]): Map<string, PlayerRow[]> {
  const rows = new Map<string, PlayerRow>();
  for (const ev of events) {
    for (const m of ev.m) {
      const name = m.sj ?? marketLabel(m);
      const team = m.tm ?? "";
      const key = `${team}|${name}`;
      const row = rows.get(key) ?? { name, team, markets: [] };
      row.markets.push({ ...m, ev });
      rows.set(key, row);
    }
  }
  const rank = (r: PlayerRow) => {
    const over = r.markets.filter((m) => (m.a ?? 0) >= 0.5 && typeof m.ln === "number").map((m) => m.ln as number);
    return (over.length ? Math.max(...over) : 0) * 100 + Math.max(0, ...r.markets.map((m) => m.a ?? 0));
  };
  const out = new Map<string, PlayerRow[]>();
  for (const r of rows.values()) {
    r.markets.sort((x, y) => (x.ln ?? -Infinity) - (y.ln ?? -Infinity) || (x.v ?? "").localeCompare(y.v ?? ""));
    out.set(r.team, [...(out.get(r.team) ?? []), r]);
  }
  for (const [k, list] of out) out.set(k, list.sort((x, y) => rank(y) - rank(x) || x.name.localeCompare(y.name)));
  return out;
}

/** A stat with at most one line per player is shown as tiles of names, not rungs. */
export function singleLine(rows: readonly PlayerRow[]): boolean {
  return rows.every((r) => r.markets.length === 1);
}

/** The latest source time of what is loaded. */
export function latestAsOf(events: readonly Pick<TabEvent, "asof">[]): string | null {
  let best: string | null = null;
  for (const e of events) if (e.asof && (!best || e.asof > best)) best = e.asof;
  return best;
}

/** The moneyline's consensus line under the header: whose number, and each side's p. */
export function winnerConsensus(ev: TabEvent): { source: string; p: (number | null)[] } {
  const qs = ev.m.map((m) => parseConsensus(m));
  // The best source any side has: books, then ESPN's book, then Kalshi's own mid.
  const first = qs.find((q) => q?.t === "b") ?? qs.find((q) => q?.t === "e") ?? qs.find(Boolean);
  const source = !first ? "Kalshi prices" : first.t === "b" ? `${first.n ?? 0} books say`
    : first.t === "e" ? "ESPN's book says" : "Kalshi mid";
  return { source, p: qs.map((q) => (q ? q.p : null)) };
}

/** The cache key of one loaded tab (and stat). */
export function tabKey(tab: Tab, stat: string | null): string {
  return tab === "players" ? `players|${stat ?? ""}` : tab;
}
