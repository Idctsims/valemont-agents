// Game Day's pure logic (Chat 7 Phase 3; docs/gameday.md): the game day,
// the sections, the order inside a day, the labels, and which rows the live
// poll asks for. No I/O: unit-tested in e2e/gameday-unit.spec.ts. The rows
// come from db/068 gameday_slate() (one compact object each).

import type { TvTier } from "./tiers";

export const TZ = "America/Chicago";
/** ESPN files a TBD event at midnight Eastern of its date. */
const ESPN_TZ = "America/New_York";
/** The game day starts at 05:00 Chicago (owner, 2026-11-04). */
export const DAY_START_HOUR = 5;

/** ESPN team id of Alabama (football), and the SEC's conference id. db/068 sets `bama`/`sec` from them. */
export const ALABAMA_TEAM_ID = "333";
export const SEC_CONFERENCE_ID = "8";

export type Status = "scheduled" | "live" | "delayed" | "suspended" | "final" | "postponed" | "canceled";
export type Kind = "game" | "race_session" | "golf_round" | "bout" | "match";

export type Golfer = { name: string; pos?: string; to_par?: string; today?: string; thru?: number | string };
export type Score = {
  home?: number | string;
  away?: number | string;
  round?: number;
  leader?: Golfer & { time?: string };
  followed?: Golfer[];
  sets?: (number | string)[][];
};

export type SlateRow = {
  id: number;
  league: string;
  day: string;
  at: string;
  tbd?: boolean;
  title?: string;
  session?: string;
  home?: string;
  away?: string;
  neutral?: boolean;
  hr?: number;
  ar?: number;
  sec?: boolean;
  bama?: boolean;
  tv?: string[];
  tier: TvTier;
  status: Status;
  detail?: string;
  clock?: string;
  score?: Score;
  test?: boolean;
};

/** db/068 gameday_live(): what moves in play. */
export type LivePatch = Pick<SlateRow, "id" | "status" | "detail" | "clock" | "score">;

/** The registry's leagues (db/065): label, a short tag, kind, and order. The five first. */
export const LEAGUES: Readonly<Record<string, { label: string; short: string; kind: Kind; sort: number }>> = {
  cfb: { label: "College Football", short: "CFB", kind: "game", sort: 10 },
  nfl: { label: "NFL", short: "NFL", kind: "game", sort: 20 },
  nba: { label: "NBA", short: "NBA", kind: "game", sort: 30 },
  f1: { label: "Formula 1", short: "F1", kind: "race_session", sort: 40 },
  pga: { label: "PGA Tour", short: "Golf", kind: "golf_round", sort: 50 },
  mcbb: { label: "Men's College Basketball", short: "NCAAB", kind: "game", sort: 60 },
  nhl: { label: "NHL", short: "NHL", kind: "game", sort: 70 },
  mlb: { label: "MLB", short: "MLB", kind: "game", sort: 80 },
  epl: { label: "Premier League", short: "EPL", kind: "game", sort: 90 },
  mls: { label: "MLS", short: "MLS", kind: "game", sort: 100 },
  ucl: { label: "Champions League", short: "UCL", kind: "game", sort: 110 },
  atp: { label: "ATP", short: "ATP", kind: "match", sort: 120 },
  wta: { label: "WTA", short: "WTA", kind: "match", sort: 130 },
  ufc: { label: "UFC", short: "UFC", kind: "bout", sort: 140 },
};

/** The owner's five, in the owner's order (CFB, NFL, NBA, F1, golf). */
export const FIVE = ["cfb", "nfl", "nba", "f1", "pga"] as const;

export function kindOf(league: string): Kind {
  return LEAGUES[league]?.kind ?? "game";
}

// ------------------------------------------------------------------ the game day

function parts(at: Date, timeZone: string): { date: string; hour: number } {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
    }).formatToParts(at).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, hour: Number(p.hour) };
}

/** YYYY-MM-DD plus n days (calendar arithmetic, no time zone). */
export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/**
 * The game day an event belongs to (db/068 gameday_day): a timed event's
 * Chicago date, where 00:00–04:59 is still the night before (wall clock, so
 * a DST morning files the same as the database); a TBD event's ESPN (Eastern)
 * date, because ESPN files it at midnight Eastern, 23:00 Chicago the evening
 * before.
 */
export function gameDay(at: Date | string, tbd = false): string {
  const instant = typeof at === "string" ? new Date(at) : at;
  if (tbd) return parts(instant, ESPN_TZ).date;
  const { date, hour } = parts(instant, TZ);
  return hour < DAY_START_HOUR ? addDays(date, -1) : date;
}

export function isLive(r: Pick<SlateRow, "status">): boolean {
  return r.status === "live" || r.status === "delayed";
}

// ------------------------------------------------------------------ sections

export type Sections = {
  live: SlateRow[];
  tonight: SlateRow[];
  week: { day: string; rows: SlateRow[] }[];
};

/** Live now (anything in play, any day) · Tonight (the game day) · This week (the next 7). */
export function sectionize(rows: readonly SlateRow[], today: string): Sections {
  const live: SlateRow[] = [];
  const tonight: SlateRow[] = [];
  const week = new Map<string, SlateRow[]>();
  const last = addDays(today, 7);
  for (const r of rows) {
    if (isLive(r)) live.push(r);
    else if (r.day === today) tonight.push(r);
    else if (r.day > today && r.day <= last) week.set(r.day, [...(week.get(r.day) ?? []), r]);
  }
  return {
    live: live.sort(compareInDay),
    tonight: tonight.sort(compareInDay),
    week: [...week.keys()].sort().map((day) => ({ day, rows: week.get(day)!.sort(compareInDay) })),
  };
}

// ------------------------------------------------------------------ order inside a day

/**
 * The order inside a day: the NFL leads whenever it plays (owner, 2026-11-05;
 * db/069 does the same in the brief), then the rest of the five in the
 * owner's order. On a night with no NFL game this is the owner's order.
 */
export const DAY_ORDER = ["nfl", "cfb", "nba", "f1", "pga"] as const;

function leagueRank(league: string): number {
  const i = (DAY_ORDER as readonly string[]).indexOf(league);
  return i >= 0 ? i : 10 + (LEAGUES[league]?.sort ?? 999);
}

/** Inside CFB: ranked, then SEC, then national TV (tier 1–2), then the rest. */
function cfbBucket(r: SlateRow): number {
  if (r.league !== "cfb") return 0;
  if (r.hr != null || r.ar != null) return 0;
  if (r.sec) return 1;
  if (r.tier <= 2) return 2;
  return 3;
}

/**
 * Owner, 2026-11-04/05: live first; Alabama; then the NFL whenever it plays;
 * then CFB, NBA, F1, golf, inside CFB ranked → SEC → national TV; then the
 * other leagues in registry order. Ties: start time, then the bigger network,
 * then id.
 */
export function compareInDay(a: SlateRow, b: SlateRow): number {
  const key = (r: SlateRow) => [
    isLive(r) ? 0 : 1,
    r.bama ? 0 : 1,
    leagueRank(r.league),
    cfbBucket(r),
    Date.parse(r.at),
    r.tier,
    r.id,
  ];
  const ka = key(a);
  const kb = key(b);
  for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
  return 0;
}

// ------------------------------------------------------------------ labels

/** "7:00 PM" in Chicago, or "TBD" when ESPN has no time (never the stored placeholder). */
export function startLabel(r: Pick<SlateRow, "at" | "tbd">): string {
  if (r.tbd) return "TBD";
  return new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" }).format(new Date(r.at));
}

/** "Tomorrow" or "Sat, Nov 7" for a game day after today. */
export function dayLabel(day: string, today: string): string {
  if (day === today) return "Tonight";
  if (day === addDays(today, 1)) return "Tomorrow";
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" })
    .format(new Date(`${day}T12:00:00Z`));
}

/** "Thu, Nov 5". */
export function dateLabel(day: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" })
    .format(new Date(`${day}T12:00:00Z`));
}

export function tvLabel(tv: readonly string[] | null | undefined): string {
  return tv && tv.length ? tv.join(" · ") : "Not listed";
}

export function statusLabel(r: Pick<SlateRow, "status" | "detail">): string | null {
  switch (r.status) {
    case "live":
      return r.detail ?? "Live";
    case "delayed":
      return r.detail ?? "Delayed";
    case "final":
      return r.detail ?? "Final";
    case "postponed":
      return "Postponed";
    case "canceled":
      return "Canceled";
    case "suspended":
      return r.detail ?? "Suspended";
    default:
      return null;
  }
}

export function rankPrefix(rank: number | undefined): string {
  return rank != null ? `No. ${rank} ` : "";
}

/** The slate in the owner's words: "No. 5 Alabama Crimson Tide at No. 14 LSU Tigers", or the event's title. */
export function matchup(r: Pick<SlateRow, "home" | "away" | "hr" | "ar" | "neutral" | "title" | "session">): string {
  if (r.home && r.away) return `${rankPrefix(r.ar)}${r.away} ${r.neutral ? "vs" : "at"} ${rankPrefix(r.hr)}${r.home}`;
  return [r.title, r.session].filter(Boolean).join(" · ");
}

// ------------------------------------------------------------------ the live poll

/** At most this many ids per poll (db/068 gameday_live reads the first 60). */
export const POLL_MAX = 60;
export const POLL_SECONDS = 60;

/**
 * The rows the live poll asks for: those in play, plus a timed game that
 * should have started (from 5 min before to 3 h after its start: the worker's
 * own hot window), so a tip-off flips to Live without a refresh. Nothing else.
 */
export function pollIds(rows: readonly SlateRow[], now: number): number[] {
  const out: number[] = [];
  for (const r of rows) {
    const start = Date.parse(r.at);
    const due = r.status === "scheduled" && !r.tbd && start - 5 * 60_000 <= now && now < start + 3 * 3_600_000;
    if (isLive(r) || due) out.push(r.id);
    if (out.length >= POLL_MAX) break;
  }
  return out;
}

/** The rows with each patch applied. Ids not polled are untouched; a patch for an unknown id is ignored. */
export function applyPatches(rows: readonly SlateRow[], patches: readonly LivePatch[]): SlateRow[] {
  const byId = new Map(patches.map((p) => [p.id, p]));
  return rows.map((r) => {
    const p = byId.get(r.id);
    if (!p) return r;
    // A field the poll leaves out is empty now (db/068 strips nulls).
    return { ...r, status: p.status, detail: p.detail, clock: p.clock, score: p.score };
  });
}

// ------------------------------------------------------------------ Kalshi

/**
 * kalshi.com's page for an event. The path is /markets/<series>/<slug>/<event>;
 * the slug is descriptive. UNVERIFIED from here (kalshi.com answers scripted
 * requests 429): the owner's tap on the phone is the check (docs/gameday.md §9).
 */
export function kalshiUrl(series: string, ticker: string): string {
  const s = series.toLowerCase();
  return `https://kalshi.com/markets/${s}/${s}/${ticker.toLowerCase()}`;
}
