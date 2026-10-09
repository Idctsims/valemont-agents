// Capital Tracker (db/026): shapes and the pure rules the page, the
// optimistic client and the unit specs share. Money is kept in CENTS
// (integers) on the client: a float sum of 0.1 + 0.2 is not a bankroll.
//
// Paper and live are never summed. Every function here takes or returns one
// mode's numbers; there is deliberately no helper that combines two modes.

export const MODES = ["paper", "live"] as const;
export type Mode = (typeof MODES)[number];

export const KINDS = ["deposit", "withdrawal", "adjustment"] as const;
export type Kind = (typeof KINDS)[number];

/** One source's row from v_capital_today, or the mode's total row. */
export type SourceToday = {
  source: string;
  /** Cents. */
  value: number;
  /** Cents at the latest snapshot before today; null when there is none. */
  prior: number | null;
};

export type ModeToday = {
  mode: Mode;
  total: SourceToday;
  sources: SourceToday[];
};

export type Entry = {
  id: string;
  mode: Mode;
  kind: Kind;
  /** Cents, as stored: positive for deposit and withdrawal. */
  amount: number;
  note: string | null;
  created_at: string;
  pending?: boolean;
};

export type Point = { date: string; value: number };

/** '1000.00' or 1000 (numeric from PostgREST) → 100000 cents. */
export function toCents(v: string | number): number {
  return Math.round(Number(v) * 100);
}

/** Cents → '1000.00', the exact text sent to a numeric(14,2) column. */
export function centsToDecimal(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** What an entry does to the balance: withdrawals subtract. */
export function signedCents(e: Pick<Entry, "kind" | "amount">): number {
  return e.kind === "withdrawal" ? -e.amount : e.amount;
}

const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** '$1,000.00', '−$40.00' (a true minus sign). */
export function money(cents: number): string {
  const text = USD.format(Math.abs(cents) / 100);
  return cents < 0 ? `−${text}` : text;
}

/** '+$250.00' / '−$40.00' / '$0.00'. */
export function signedMoney(cents: number): string {
  if (cents === 0) return money(0);
  return `${cents > 0 ? "+" : "−"}${money(Math.abs(cents))}`;
}

export type Change = { cents: number; pct: number | null; tone: "up" | "down" | "flat" };

/** Today's change against the latest snapshot; null when there is none. */
export function changeOf(t: SourceToday): Change | null {
  if (t.prior === null) return null;
  const cents = t.value - t.prior;
  return {
    cents,
    pct: t.prior === 0 ? null : cents / Math.abs(t.prior),
    tone: cents > 0 ? "up" : cents < 0 ? "down" : "flat",
  };
}

/** '+$250.00 · +25.0% today', or '—' with no prior snapshot. */
export function changeLabel(c: Change | null): string {
  if (!c) return "—";
  const pct =
    c.pct === null ? "" : ` · ${c.pct > 0 ? "+" : c.pct < 0 ? "−" : ""}${(Math.abs(c.pct) * 100).toFixed(1)}%`;
  return `${signedMoney(c.cents)}${pct} today`;
}

/** Display name for a source key: 'bankroll' → 'Bankroll'. */
export function sourceLabel(source: string): string {
  const s = source.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Share of the mode's total, 0..1, for the thin bar. Never across modes. */
export function share(part: number, total: number): number {
  if (total <= 0 || part <= 0) return 0;
  return Math.min(1, part / total);
}

/**
 * Apply an entry the server has not confirmed yet to one mode's numbers:
 * the bankroll source and the total move by its signed amount, the prior
 * snapshot stays where it was. Entries of the other mode are ignored.
 */
export function applyEntry(today: ModeToday, e: Pick<Entry, "mode" | "kind" | "amount">): ModeToday {
  if (e.mode !== today.mode) return today;
  const delta = signedCents(e);
  const hasBankroll = today.sources.some((s) => s.source === "bankroll");
  const sources = hasBankroll
    ? today.sources.map((s) => (s.source === "bankroll" ? { ...s, value: s.value + delta } : s))
    : [...today.sources, { source: "bankroll", value: delta, prior: today.total.prior === null ? null : 0 }];
  return { ...today, sources, total: { ...today.total, value: today.total.value + delta } };
}

/** An empty mode, for the first entry of a mode that had none. */
export function emptyMode(mode: Mode): ModeToday {
  return { mode, total: { source: "total", value: 0, prior: null }, sources: [] };
}

/**
 * "Correct with adjustment": the amount that undoes an entry. A deposit of
 * 50 is undone by −50, a withdrawal of 50 by +50, an adjustment by its
 * negation. Never zero: every stored entry is non-zero (db/026 CHECK).
 */
export function correctionFor(e: Pick<Entry, "kind" | "amount">): number {
  return -signedCents(e);
}

/** Parse what the amount field holds into positive cents; null if invalid. */
export function parseAmount(text: string): number | null {
  const t = text.trim().replace(/[$,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(t) && !/^\.\d{1,2}$/.test(t)) return null;
  const cents = Math.round(Number(t) * 100);
  return cents > 0 && cents < 1e14 ? cents : null;
}

export const RANGES = ["1W", "1M", "3M", "All"] as const;
export type Range = (typeof RANGES)[number];
const RANGE_DAYS: Record<Exclude<Range, "All">, number> = { "1W": 7, "1M": 30, "3M": 91 };

/**
 * The chart's points: one per snapshot date (the mode's sources summed for
 * that date, one mode only), then today's live value. `snapshots` holds one
 * mode's rows.
 */
export function series(
  snapshots: { snap_date: string; value: number }[],
  today: string,
  nowValue: number,
): Point[] {
  const byDate = new Map<string, number>();
  for (const s of snapshots) {
    if (s.snap_date >= today) continue;
    byDate.set(s.snap_date, (byDate.get(s.snap_date) ?? 0) + s.value);
  }
  const points = [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, value]) => ({ date, value }));
  return [...points, { date: today, value: nowValue }];
}

/** Points on or after the range's first day (today counts as day one). */
export function inRange(points: Point[], range: Range, today: string): Point[] {
  if (range === "All") return points;
  const from = new Date(`${today}T00:00:00Z`);
  from.setUTCDate(from.getUTCDate() - (RANGE_DAYS[range] - 1));
  const first = from.toISOString().slice(0, 10);
  return points.filter((p) => p.date >= first);
}

/** 'Oct 9 · 2:41 PM', in the owner's timezone. */
export function entryStamp(iso: string, tz = "America/Chicago"): string {
  const d = new Date(iso);
  const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: tz }).format(d);
  const time = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz }).format(d);
  return `${day} · ${time}`;
}

/** 'Oct 9' for a 'YYYY-MM-DD' date. */
export function shortDate(day: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${day}T00:00:00Z`),
  );
}
