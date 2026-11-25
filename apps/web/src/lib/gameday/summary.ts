// A game's ESPN summary, cut down to what the Google-style game page shows
// (owner, Nov 21: "match everything else to how Google does it"): records,
// the score by period, football's down and distance with the ball's spot,
// the last play, game leaders, team stats, player stats, the scoring
// summary and the play-by-play. Fetched on open, never stored (espn.ts).
// Pure: no I/O. Every section is optional: an absent or odd field drops its
// section, never the page.

export type Side = "away" | "home";

export type SideLine = {
  abbr: string;
  record?: string;
  rank?: number;
  /** Points per period, as ESPN writes them. */
  lines: string[];
  score?: string;
  possession?: boolean;
};

export type Situation = {
  /** "1st & 10 at MIA 35" */
  text: string;
  offense: Side;
  /** The ball's spot in yards from the away end zone (left, 0) to the home one (right, 100). */
  x: number;
  /** The first-down line on the same axis, when there is a distance to go. */
  toGo?: number;
  redZone?: boolean;
  drive?: string;
};

export type Play = {
  period: string;
  clock?: string;
  side?: Side;
  text: string;
  away?: number;
  home?: number;
  scoring?: boolean;
  type?: string;
};

export type Leader = { side: Side; category: string; name: string; line: string };
export type StatRow = {
  name: string;
  jersey?: string;
  stats: string[];
  /** The Parlays page's live count (Phase 4.5): ESPN's full name and photo. */
  full?: string;
  img?: string;
};
/** `key` is ESPN's own group name ("passing", "forwards"; "" for basketball's one group). */
export type StatGroup = { name: string; key?: string; labels: string[]; rows: StatRow[] };

export type GameSummary = {
  away: SideLine;
  home: SideLine;
  /** Column labels for the score table: 1 2 3 4 (OT…). */
  periods: string[];
  /** ESPN's game state: pre, in, post. */
  state?: "pre" | "in" | "post";
  /** ESPN's status line, as the database stores it ("10:09 - 2nd Quarter", "Halftime", "Final"). */
  detail?: string;
  note?: string;
  situation?: Situation;
  lastPlay?: Play;
  leaders: Leader[];
  teamStats: { label: string; away: string; home: string }[];
  players: Record<Side, StatGroup[]>;
  scoring: Play[];
  /** Newest first, capped. */
  plays: Play[];
  venue?: string;
  attendance?: number;
  weather?: string;
};

export const PLAYS_MAX = 150;

/** Regulation periods by league, for the score table's columns. */
const REGULATION: Record<string, number> = {
  nfl: 4, cfb: 4, nba: 4, mcbb: 2, nhl: 3, mlb: 9, epl: 2, mls: 2, ucl: 2,
};

type J = Record<string, unknown>;

function obj(v: unknown): J | undefined {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as J) : undefined;
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function str(v: unknown): string | undefined {
  if (typeof v === "string") return v.trim() || undefined;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return undefined;
}
function num(v: unknown): number | undefined {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : undefined;
}
function at(o: unknown, ...path: (string | number)[]): unknown {
  let cur: unknown = o;
  for (const k of path) {
    if (typeof k === "number") cur = arr(cur)[k];
    else cur = obj(cur)?.[k];
    if (cur === undefined) return undefined;
  }
  return cur;
}

/** "1 2 3 4", then OT, 2OT … past regulation (extra innings stay numbered). */
export function periodLabels(league: string, played: number): string[] {
  const reg = REGULATION[league] ?? 4;
  const n = Math.max(reg, played);
  return Array.from({ length: n }, (_, i) => {
    if (i < reg || league === "mlb") return String(i + 1);
    const ot = i - reg + 1;
    return ot === 1 ? "OT" : `${ot}OT`;
  });
}

const UNIT: Record<string, string> = {
  nfl: "Quarter", cfb: "Quarter", nba: "Quarter", mcbb: "Half", nhl: "Period", mlb: "Inning", epl: "Half", mls: "Half", ucl: "Half",
};

function ordinal(n: number): string {
  const t = n % 100;
  if (t >= 11 && t <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

/** A period's name over its plays: ESPN's when given, else "2nd Quarter", "Overtime" past regulation. */
export function periodName(league: string, p: unknown): string {
  const given = str(at(p, "displayValue"));
  if (given) return given;
  const n = num(at(p, "number"));
  if (n == null) return "";
  const reg = REGULATION[league] ?? 4;
  if (n > reg && league !== "mlb") return n === reg + 1 ? "Overtime" : `${ordinal(n - reg)} Overtime`;
  return `${ordinal(n)} ${UNIT[league] ?? "Period"}`;
}

/** "D. Raiola" from ESPN's short name, else from the full one. */
export function shortName(full: string | undefined, short: string | undefined): string {
  if (short) return short;
  if (!full) return "";
  const parts = full.split(/\s+/);
  if (parts.length < 2) return full;
  // "Dillon Hill Jr." -> "D. Hill Jr.": everything after the first name stays.
  return `${parts[0][0]}. ${parts.slice(1).join(" ")}`;
}

/** Football's play text leads with its own clock, "(10:10) ", which the row already shows. */
function playText(t: string): string {
  return t.replace(/^\(\d{1,2}:\d{2}\)\s*/, "");
}

/** The ball's spot on the away-left axis: an away offense drives right, a home offense left. */
export function fieldX(offense: Side, yardsToEndzone: number): number {
  const x = offense === "away" ? 100 - yardsToEndzone : yardsToEndzone;
  return Math.min(100, Math.max(0, x));
}

function short(name: string | undefined, a: J | undefined): string {
  return shortName(name, str(a?.shortName));
}

export function parseSummary(raw: unknown, league: string): GameSummary | null {
  const comp = obj(at(raw, "header", "competitions", 0));
  if (!comp) return null;
  const sideOf = new Map<string, Side>();
  const lines: Partial<Record<Side, SideLine>> = {};
  for (const c of arr(comp.competitors)) {
    const side = str(at(c, "homeAway"));
    if (side !== "home" && side !== "away") continue;
    const id = str(at(c, "id")) ?? str(at(c, "team", "id"));
    if (id) sideOf.set(id, side);
    const records = arr(at(c, "record"));
    const total = records.find((r) => str(at(r, "type")) === "total") ?? records[0];
    lines[side] = {
      abbr: str(at(c, "team", "abbreviation")) ?? str(at(c, "team", "shortDisplayName")) ?? side.toUpperCase(),
      record: str(at(total, "summary")) ?? str(at(total, "displayValue")),
      rank: num(at(c, "rank")) && num(at(c, "rank"))! <= 25 ? num(at(c, "rank")) : undefined,
      lines: arr(at(c, "linescores")).map((l) => str(at(l, "displayValue")) ?? str(at(l, "value")) ?? "-"),
      score: str(at(c, "score")),
      possession: at(c, "possession") === true || undefined,
    };
  }
  if (!lines.away || !lines.home) return null;
  const side = (teamId: unknown): Side | undefined => (str(teamId) ? sideOf.get(str(teamId)!) : undefined);

  const state = str(at(comp, "status", "type", "state"));
  const played = Math.max(lines.away.lines.length, lines.home.lines.length);

  // Plays: basketball, hockey and soccer list them at the top; football inside its drives.
  const playOf = (p: unknown): Play | null => {
    const raw = str(at(p, "text"));
    const text = raw ? playText(raw) : undefined;
    if (!text) return null;
    return {
      period: periodName(league, at(p, "period")),
      clock: str(at(p, "clock", "displayValue")),
      side: side(at(p, "team", "id")) ?? side(at(p, "start", "team", "id")),
      text,
      away: num(at(p, "awayScore")),
      home: num(at(p, "homeScore")),
      scoring: at(p, "scoringPlay") === true || undefined,
      type: str(at(p, "type", "text")),
    };
  };
  let plays: Play[] = [];
  const top = arr(at(raw, "plays"));
  if (top.length) {
    plays = top.map(playOf).filter((p): p is Play => !!p);
  } else {
    const drives = [...arr(at(raw, "drives", "previous")), ...(obj(at(raw, "drives", "current")) ? [at(raw, "drives", "current")] : [])];
    // The current drive is also the last of "previous" once it ends; keep each play once.
    const seen = new Set<string>();
    for (const d of drives) {
      for (const p of arr(at(d, "plays"))) {
        const id = str(at(p, "id"));
        if (id && seen.has(id)) continue;
        if (id) seen.add(id);
        const play = playOf(p);
        if (play && !play.side) play.side = side(at(d, "team", "id"));
        if (play) plays.push(play);
      }
    }
  }
  plays.reverse();

  // Football's down and distance: the current drive's last play's end.
  let situation: Situation | undefined;
  const cur = obj(at(raw, "drives", "current"));
  if (state === "in" && cur) {
    const last = arr(cur.plays).at(-1);
    const end = obj(at(last, "end"));
    const offense = side(at(end, "team", "id")) ?? side(at(cur, "team", "id"));
    const yte = num(end?.yardsToEndzone);
    const text = str(end?.downDistanceText);
    if (offense && yte != null && text) {
      const x = fieldX(offense, yte);
      const dist = num(end?.distance);
      situation = {
        text, offense, x,
        toGo: dist && dist > 0 && dist < yte ? fieldX(offense, yte - dist) : undefined,
        redZone: yte <= 20 || undefined,
        drive: str(cur.description),
      };
    }
  }

  const leaders: Leader[] = [];
  for (const t of arr(at(raw, "leaders"))) {
    const s = side(at(t, "team", "id"));
    if (!s) continue;
    for (const cat of arr(at(t, "leaders"))) {
      const top1 = at(cat, "leaders", 0);
      const name = short(str(at(top1, "athlete", "displayName")), obj(at(top1, "athlete")));
      const value = str(at(top1, "displayValue"));
      const category = str(at(cat, "displayName"));
      if (!name || !value || !category) continue;
      leaders.push({ side: s, category, name, line: value });
    }
  }

  const teamStats: GameSummary["teamStats"] = [];
  const boxTeams = arr(at(raw, "boxscore", "teams"));
  const statsBy = new Map<Side, J[]>();
  for (const t of boxTeams) {
    const s = side(at(t, "team", "id")) ?? (str(at(t, "homeAway")) as Side | undefined);
    if (s === "home" || s === "away") statsBy.set(s, arr(at(t, "statistics")).map(obj).filter((x): x is J => !!x));
  }
  const homeStats = new Map((statsBy.get("home") ?? []).map((st) => [str(st.name) ?? str(st.label), st]));
  for (const st of statsBy.get("away") ?? []) {
    const key = str(st.name) ?? str(st.label);
    const label = str(st.label);
    const h = key ? homeStats.get(key) : undefined;
    const a = str(st.displayValue);
    const hv = str(h?.displayValue);
    if (label && a != null && hv != null) teamStats.push({ label, away: a, home: hv });
  }

  const players: Record<Side, StatGroup[]> = { away: [], home: [] };
  for (const t of arr(at(raw, "boxscore", "players"))) {
    const s = side(at(t, "team", "id"));
    if (!s) continue;
    for (const g of arr(at(t, "statistics"))) {
      const labels = arr(at(g, "labels")).map(str).filter((x): x is string => !!x);
      if (!labels.length) continue;
      const rows = arr(at(g, "athletes"))
        .filter((a) => at(a, "didNotPlay") !== true)
        .map((a) => {
          const stats = arr(at(a, "stats")).slice(0, labels.length).map((v) => str(v) ?? "");
          const img = str(at(a, "athlete", "headshot", "href"));
          return {
            name: short(str(at(a, "athlete", "displayName")), obj(at(a, "athlete"))),
            jersey: str(at(a, "athlete", "jersey")),
            stats,
            full: str(at(a, "athlete", "displayName")),
            ...(img && img.startsWith("https://a.espncdn.com/") ? { img } : {}),
          };
        })
        .filter((r) => r.name && r.stats.some((v) => v !== "" && v !== "--"));
      if (!rows.length) continue;
      // "passing" -> "Passing", "kickReturns" -> "Kick returns"; a sport with one group (basketball) has no name.
      const name = (str(at(g, "name")) ?? "")
        .replace(/([a-z])([A-Z])/g, (_, a: string, b: string) => `${a} ${b.toLowerCase()}`)
        .replace(/^[a-z]/, (c) => c.toUpperCase());
      players[s].push({ name, key: str(at(g, "name")) ?? "", labels, rows });
    }
  }

  const scoring = arr(at(raw, "scoringPlays")).map(playOf).filter((p): p is Play => !!p);

  const info = obj(at(raw, "gameInfo"));
  const city = [str(at(info, "venue", "address", "city")), str(at(info, "venue", "address", "state"))].filter(Boolean).join(", ");
  const venueName = str(at(info, "venue", "fullName"));
  const temp = num(at(info, "weather", "temperature"));
  const cond = str(at(info, "weather", "conditionId"));

  const week = num(at(raw, "header", "week"));
  const football = league === "nfl" || league === "cfb";

  return {
    away: lines.away,
    home: lines.home,
    periods: periodLabels(league, played),
    state: state === "pre" || state === "in" || state === "post" ? state : undefined,
    // The short form ("8:18 - 2nd") is the one the database stores and liveClock reads.
    detail: str(at(comp, "status", "type", "shortDetail")) ?? str(at(comp, "status", "type", "detail")),
    note: football && week ? `Week ${week}` : undefined,
    situation,
    lastPlay: state === "in" ? plays.find((p) => !/^(end of|timeout|official tv)/i.test(p.text)) : undefined,
    leaders,
    teamStats,
    players,
    scoring,
    plays: plays.slice(0, PLAYS_MAX),
    venue: venueName ? (city ? `${venueName}, ${city}` : venueName) : undefined,
    attendance: num(at(info, "attendance")) || undefined,
    weather: temp != null ? `${temp}°${cond ? `, ${cond.toLowerCase()}` : ""}` : undefined,
  };
}
