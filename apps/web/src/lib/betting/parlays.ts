// The Parlays page (Chat 8 Phase 4.5; docs/parlays_page.md): what db/091's
// parlay_day returns, into what a ticket card shows. Pure: no I/O.
//
// - Legs in plain words (§5), never tickers: a deterministic phrasebook over
//   the leg's display fields, else Kalshi's own title with its side.
// - The live count (§4), like PrizePicks: a prop counts the player's stat in
//   ESPN's box score; a game line counts the score. A YES "N+" clears the
//   moment the count reaches N; a NO busts there and clears at the final. The
//   count is a snapshot: the ticket's ✓ / ✕ and its outcome come from the
//   resolution (Kalshi's settlement), never from here.
// - Money: the ledger's own paper stake to win the payout; the multiplier is
//   their ratio. No cents on the page (owner, Nov 24).

export type LegSide = "yes" | "no";
export type GameSide = "home" | "away";

export type ParlayLeg = {
  side: LegSide;
  /** Kalshi's title with the game in parentheses, as frozen in the reason line. */
  label: string;
  kind?: string;
  /** The player (a prop) or the team (a game line), from the board. */
  who?: string;
  stat?: string;
  line?: number;
  team?: string;
  /** A period line: 1H, 2H, 1Q.., 1P.. (db/079's kalshi_board_period). */
  per?: string;
  /** The game's title when it has no Game Day page. */
  gt?: string;
  /** The Game Day id (sports_events.id). */
  g?: number;
  /** The leg's team in its linked game. */
  sd?: GameSide;
  /** Kickoff at commit (epoch seconds). */
  start?: number;
  /** The settled value of the leg's side (1 hit, 0 miss, between: a fair-price settlement). */
  res?: number;
  /** Read back from the frozen label (inferLeg), not from captured display fields. */
  inferred?: boolean;
};

export type ParlayTicket = {
  id: number;
  tier: "safer" | "mid" | "lotto";
  scope: "sport" | "cross_sport";
  sport?: string;
  rv: string;
  slot: "10:45" | "16:45";
  at: number;
  stake: number;
  pays: number;
  outcome?: "hit" | "miss" | "partial";
  pnl?: number;
  paid?: number;
  fill?: string;
  combo?: string;
  legs: ParlayLeg[];
};

export type GameTeam = { id?: string; abbr?: string; loc?: string; nick?: string; name?: string; logo?: string; color?: string };
export type ParlayGame = {
  lg: string;
  st?: string;
  per?: number;
  clk?: string;
  sc?: { home?: number; away?: number };
  start?: number;
  home?: GameTeam;
  away?: GameTeam;
};

export type SlotResult = {
  slot: "10:45" | "16:45";
  tier: string;
  scope?: string;
  sport?: string;
  rule: string;
  raw?: string;
  rv?: string;
};

export type ParlayDay = {
  day: string;
  asof: number;
  enabled?: boolean;
  prev?: string;
  next?: string;
  tickets: ParlayTicket[];
  games: Record<string, ParlayGame>;
  results: SlotResult[];
};

export const TIER_NAME: Record<string, string> = { safer: "Safer", mid: "Mid", lotto: "Lotto" };
export const TIER_LEGS: Record<string, string> = { safer: "3–5 legs", mid: "6–10 legs", lotto: "11–20 legs" };
const TIER_ORDER: Record<string, number> = { safer: 1, mid: 2, lotto: 3 };

// --------------------------------------------------------------------------- money

/** "$41.34" (whole dollars stay "$100"). */
export function usd(x: number): string {
  const whole = Math.abs(x - Math.round(x)) < 0.005;
  return `$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 })}`;
}

/** "+$58.66" / "−$41.34" (a real minus). */
export function signedUsd(x: number): string {
  return `${x < 0 ? "−" : "+"}${usd(x)}`;
}

/** What the paper stake returns if every leg hits: the payout over the stake. */
export function multiplier(t: Pick<ParlayTicket, "stake" | "pays">): number {
  return t.stake > 0 ? t.pays / t.stake : 0;
}

export function multText(t: Pick<ParlayTicket, "stake" | "pays">): string {
  const m = multiplier(t);
  return `×${m >= 100 ? m.toFixed(0) : m.toFixed(2)}`;
}

/** R per $1 staked as words: "−$0.26 per $1 staked". */
export function perDollar(r: number): string {
  return `${r < 0 ? "−" : "+"}$${Math.abs(r).toFixed(2)} per $1 staked`;
}

// --------------------------------------------------------------------------- legs in words

/** The board's stat name as the card says it; "(protected)" is Kalshi's variant, not a stat. */
const STAT_WORDS: Record<string, string> = {
  points: "pts", rebounds: "reb", assists: "ast", threes: "threes", steals: "stl", blocks: "blk",
  "combined points, rebounds, and assists": "pts + reb + ast", "combined steals and blocks": "stl + blk",
  goals: "goals", saves: "saves",
  "receiving yards": "rec yds", receptions: "receptions", "rushing yards": "rush yds", "passing yards": "pass yds",
  touchdowns: "TDs", "passing touchdowns": "pass TDs", "passing completions": "completions",
  "passing attempts": "pass attempts", "passing interceptions": "interceptions", "rushing attempts": "rush attempts",
  "rushing and receiving yards combined": "rush + rec yds",
};

/** An abbreviation reads beside a number ("15+ pts"), not after "No". */
const SPELLED: Record<string, string> = {
  pts: "points", reb: "rebounds", ast: "assists", stl: "steals", blk: "blocks", "rec yds": "receiving yards",
  "rush yds": "rushing yards", "pass yds": "passing yards",
};

const PERIODS: Record<string, string> = {
  "1H": "1st half", "2H": "2nd half", "1Q": "1st quarter", "2Q": "2nd quarter", "3Q": "3rd quarter",
  "4Q": "4th quarter", "1P": "1st period", "2P": "2nd period", "3P": "3rd period",
};

export function baseStat(stat: string | undefined): string | undefined {
  return stat?.replace(/\s*\(protected\)$/, "").trim() || undefined;
}

/** "N+" lines are written x.5 on the board: 0.5 means 1+. */
function need(line: number): number {
  return Math.floor(line) + 1;
}

/** The label without its game: "Jalen Suggs: 1+ (DET at ORL)" -> ["Jalen Suggs: 1+", "DET at ORL"]. */
export function splitLabel(label: string): [string, string | undefined] {
  const m = /^(.*\S)\s+\(([^()]+)\)$/.exec(label.trim());
  return m ? [m[1], m[2]] : [label.trim(), undefined];
}

function n(x: number): string {
  return String(x);
}

function teamName(t: GameTeam | undefined): string | undefined {
  return t?.loc ?? t?.name ?? t?.abbr;
}

/** A game's short name: "Pistons–Magic", else its title. */
export function matchupShort(game: ParlayGame | undefined, title: string | undefined): string | undefined {
  const a = game?.away?.nick ?? game?.away?.abbr;
  const h = game?.home?.nick ?? game?.home?.abbr;
  if (a && h) return `${a}–${h}`;
  return title;
}

/** "Detroit at Orlando", else the title as Kalshi writes it. */
export function matchupLong(game: ParlayGame | undefined, title: string | undefined): string | undefined {
  const a = teamName(game?.away);
  const h = teamName(game?.home);
  if (a && h) return `${a} at ${h}`;
  return title;
}

const GOAL_LEAGUES = new Set(["nhl", "epl", "mls", "ucl"]);
const POINT_LEAGUES = new Set(["nba", "nfl", "cfb", "mcbb", "wcbb", "wnba"]);

/** " goals" / " pts" after a total's line, when the game says which; nothing when it can't be told. */
function unitOf(game: ParlayGame | undefined, title: string | undefined): string {
  if (game && GOAL_LEAGUES.has(game.lg)) return " goals";
  if (game && POINT_LEAGUES.has(game.lg)) return " pts";
  return title && / vs /.test(title) ? " goals" : "";
}

/** No draw in these: a NO on one side's win is the other side's win. */
const NO_DRAW = new Set(["nba", "nfl", "nhl", "cfb", "mcbb", "wnba", "mlb", "wcbb"]);

/** consensus/parlay_ticket.py's STAT_WORDS, reversed: the word a frozen label carries, back to the board's stat. */
const LABEL_STATS: Record<string, string> = {
  pts: "points", reb: "rebounds", ast: "assists", threes: "threes", stl: "steals", blk: "blocks",
  "pts+reb+ast": "combined points, rebounds, and assists", "stl+blk": "combined steals and blocks",
};

/** A side of a linked game whose name the subject is ("Cleveland", "New York I", "CLE"). */
function sideByName(who: string, game: ParlayGame | undefined): GameSide | undefined {
  const w = who.toLowerCase();
  for (const s of ["home", "away"] as const) {
    const t = game?.[s];
    const names = [t?.loc, t?.nick, t?.name, t?.abbr].filter(Boolean).map((x) => x!.toLowerCase());
    if (names.some((n) => n === w || n.startsWith(w) || w.startsWith(n))) return s;
  }
  return undefined;
}

/**
 * A leg committed before its display fields were captured (db/091 from Nov 24) carries only its frozen label.
 * Read Kalshi's usual title shapes back into fields, marked `inferred`. A game line read this way is worded
 * but never counted: its title can drop the period ("Over 50.5 points" may be a half), and a count of the
 * wrong period would be a wrong number. A prop's "N+" is a full-game stat, so it still counts.
 */
export function inferLeg(leg: ParlayLeg, game?: ParlayGame): ParlayLeg {
  const withSide = (l: ParlayLeg): ParlayLeg =>
    !l.sd && l.who && game ? { ...l, sd: sideByName(l.who, game) } : l;
  if (leg.kind) return withSide(leg);
  const [core] = splitLabel(leg.label);
  let m: RegExpExecArray | null;
  if ((m = /^(.+?): (\d+)\+ (.+)$/.exec(core))) {
    return { ...leg, inferred: true, kind: "prop", who: m[1], line: Number(m[2]) - 0.5, stat: LABEL_STATS[m[3]] ?? m[3] };
  }
  if ((m = /^(.+?) (double-double|triple-double)$/.exec(core))) {
    return { ...leg, inferred: true, kind: "prop", who: m[1], stat: m[2].replace("-", " ") };
  }
  if ((m = /^(.+?) first goalscorer$/.exec(core))) {
    return { ...leg, inferred: true, kind: "prop", who: m[1], stat: "first goalscorer" };
  }
  if (/\b(1H|2H|1st half|2nd half|half|quarter|period)\b/i.test(core)) return leg;   // a period line: Kalshi's title as is
  if ((m = /^(.+?) wins by (?:over|more than) ([\d.]+) (?:points|goals|runs)$/.exec(core))) {
    return withSide({ ...leg, inferred: true, kind: "game_line", who: m[1], stat: "spread", line: Number(m[2]) });
  }
  if ((m = /^(.+?) wins$/.exec(core))) {
    return withSide({ ...leg, inferred: true, kind: "game_line", who: m[1], stat: "winner" });
  }
  if ((m = /^Over ([\d.]+) (?:points|goals|runs)(?: scored)?$/i.exec(core))) {
    return { ...leg, inferred: true, kind: "game_line", stat: "total", line: Number(m[1]) };
  }
  if ((m = /^(.+?) over ([\d.]+) (?:points|goals|runs)$/.exec(core))) {
    return withSide({ ...leg, inferred: true, kind: "game_line", who: m[1], stat: "team_total", line: Number(m[2]) });
  }
  return leg;
}

export type LegWords = {
  /** The big line: the player, or what the game line says. */
  title: string;
  /** Under it: the prop ("3 or fewer threes"), or the game. */
  detail?: string;
  /** The game, when the detail is the prop. */
  game?: string;
};

export function legWords(raw: ParlayLeg, game?: ParlayGame): LegWords {
  const leg = inferLeg(raw, game);
  const [core, inParens] = splitLabel(leg.label);
  const gameLong = matchupLong(game, leg.gt ?? inParens);
  const per = leg.per ? PERIODS[leg.per] : undefined;
  const no = leg.side === "no";
  const stat = baseStat(leg.stat);

  if (leg.kind === "prop" && leg.who && stat) {
    const word = STAT_WORDS[stat];
    if (stat === "double double" || stat === "triple double") {
      const what = stat.replace(" ", "-");
      return { title: leg.who, detail: no ? `No ${what}` : what[0].toUpperCase() + what.slice(1), game: gameLong };
    }
    if (stat === "first goalscorer" || stat === "1st touchdown") {
      const what = stat === "first goalscorer" ? "first goal" : "first TD";
      return { title: leg.who, detail: no ? `Not the ${what}` : `Scores the ${what}`, game: gameLong };
    }
    if (stat === "points leader") {
      return { title: leg.who, detail: no ? "Not the top scorer" : "Top scorer", game: gameLong };
    }
    if (word && typeof leg.line === "number") {
      const k = need(leg.line);
      const detail = no ? (k - 1 === 0 ? `No ${SPELLED[word] ?? word}` : `${k - 1} or fewer ${word}`) : `${k}+ ${word}`;
      return { title: leg.who, detail, game: gameLong };
    }
  }

  if (leg.kind === "game_line" && stat) {
    const lead = per ? `${per}: ` : "";
    const subject = leg.who;
    const other = leg.sd ? teamName(leg.sd === "home" ? game?.away : game?.home) : undefined;
    if (stat === "winner" && subject) {
      if (/^(tie|draw)$/i.test(subject)) return { title: `${lead}${no ? "No draw" : "Draw"}`, detail: gameLong };
      if (!no) return { title: `${lead}${subject} wins`, detail: gameLong };
      if (other && game && NO_DRAW.has(game.lg)) return { title: `${lead}${other} beats ${subject}`, detail: gameLong };
      return { title: `${lead}${subject} doesn't win`, detail: gameLong };
    }
    if (stat === "spread" && subject && typeof leg.line === "number") {
      const by = need(leg.line);
      return { title: `${lead}${subject} ${no ? "doesn't win" : "wins"} by ${by}+`, detail: gameLong };
    }
    const unit = unitOf(game, gameLong);
    if (stat === "total" && typeof leg.line === "number") {
      return { title: `${lead}${no ? "Under" : "Over"} ${n(leg.line)}${unit}`, detail: gameLong };
    }
    if (stat === "team_total" && subject && typeof leg.line === "number") {
      return { title: `${lead}${subject} ${no ? "under" : "over"} ${n(leg.line)}${unit}`, detail: gameLong };
    }
  }

  // Anything the phrasebook does not know: Kalshi's title with its side.
  return { title: no ? `No: ${core}` : core, detail: gameLong };
}

// --------------------------------------------------------------------------- the live count

/** A name for matching Kalshi to ESPN: accents, punctuation and suffixes off. "Egor Dëmin" = "Egor Demin". */
export function normName(name: string): string {
  return name
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[.'’]/g, "")
    .replace(/\b(jr|sr|ii|iii|iv)\b/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** One player's box score: "group:LABEL" -> ESPN's value ("passing:YDS" -> "212"; basketball's group is ""). */
export type PlayerLive = { img?: string; side?: GameSide; stats?: Record<string, string> };

/** One game, live: ESPN's state, its status line, and each side's score by period. */
export type GameLive = {
  state: "pre" | "in" | "post";
  detail?: string;
  away?: { score?: number; lines?: number[] };
  home?: { score?: number; lines?: number[] };
  /** Keyed by normName. */
  players: Record<string, PlayerLive>;
};

function v(stats: Record<string, string> | undefined, ...keys: string[]): number | undefined {
  if (!stats) return undefined;
  for (const k of keys) {
    const raw = stats[k];
    if (raw == null || raw === "" || raw === "--") continue;
    const made = /^(\d+)-\d+$/.exec(raw);   // "4-6" (made-attempted): the made part
    const x = made ? Number(made[1]) : Number(raw);
    if (Number.isFinite(x)) return x;
  }
  return undefined;
}

function sum(...xs: (number | undefined)[]): number | undefined {
  return xs.some((x) => x === undefined) ? undefined : xs.reduce<number>((a, b) => a + (b as number), 0);
}

/** The board's stat, counted from ESPN's box score. undefined: not counted (the leg shows its clock). */
export function statNow(stat: string, stats: Record<string, string> | undefined): number | undefined {
  const s = baseStat(stat);
  switch (s) {
    case "points": return v(stats, ":PTS") ?? sum(v(stats, "forwards:G", "defenses:G", "skaters:G", ":G"), v(stats, "forwards:A", "defenses:A", "skaters:A", ":A"));
    case "rebounds": return v(stats, ":REB");
    case "assists": return v(stats, ":AST", "forwards:A", "defenses:A", "skaters:A", ":A");
    case "threes": return v(stats, ":3PT");
    case "steals": return v(stats, ":STL");
    case "blocks": return v(stats, ":BLK");
    case "combined points, rebounds, and assists": return sum(v(stats, ":PTS"), v(stats, ":REB"), v(stats, ":AST"));
    case "combined steals and blocks": return sum(v(stats, ":STL"), v(stats, ":BLK"));
    case "goals": return v(stats, "forwards:G", "defenses:G", "skaters:G", ":G");
    case "saves": return v(stats, "goalies:SV", ":SV");
    case "receiving yards": return v(stats, "receiving:YDS");
    case "receptions": return v(stats, "receiving:REC");
    case "rushing yards": return v(stats, "rushing:YDS");
    case "passing yards": return v(stats, "passing:YDS");
    case "touchdowns": return sum(v(stats, "rushing:TD") ?? 0, v(stats, "receiving:TD") ?? 0);
    case "passing touchdowns": return v(stats, "passing:TD");
    case "passing completions": return v(stats, "passing:C/ATT");
    case "passing attempts": {
      const raw = stats?.["passing:C/ATT"];
      const m = raw ? /^\d+\/(\d+)$/.exec(raw) : null;
      return m ? Number(m[1]) : undefined;
    }
    case "passing interceptions": return v(stats, "passing:INT");
    case "rushing attempts": return v(stats, "rushing:CAR");
    case "rushing and receiving yards combined": return sum(v(stats, "rushing:YDS") ?? 0, v(stats, "receiving:YDS") ?? 0);
    default: return undefined;
  }
}

function doubles(stats: Record<string, string> | undefined): number | undefined {
  const xs = [":PTS", ":REB", ":AST", ":STL", ":BLK"].map((k) => v(stats, k));
  if (xs[0] === undefined) return undefined;
  return xs.filter((x) => (x ?? 0) >= 10).length;
}

/** A side's points over a period line's periods: 1H the first half's, 2H the second's (regulation only). */
function periodScore(lines: number[] | undefined, per: string, lg: string): number | undefined {
  if (!lines) return undefined;
  const halves = lg === "mcbb" || lg === "wcbb" || lg === "epl" || lg === "mls" || lg === "ucl";
  const pick = (idx: number[]) => (idx.every((i) => i < lines.length) ? idx.reduce((a, i) => a + lines[i], 0) : undefined);
  const m = /^([1-4])([QP])$/.exec(per);
  if (m) return pick([Number(m[1]) - 1]);
  if (per === "1H") return pick(halves ? [0] : [0, 1]);
  if (per === "2H") return pick(halves ? [1] : [2, 3]);
  return undefined;
}

export type LegState = "pre" | "live" | "cleared" | "danger" | "busted" | "hit" | "miss" | "settled";

export type LegLive = {
  state: LegState;
  /** The count and what it is counting to; a bar fills now/target. */
  now?: number;
  target?: number;
  /** A NO leg busts at its target: the bar's end is the line it must stay under. */
  under?: boolean;
  /** "needs 1", "one more busts it", "leads by 4", the clock. */
  note?: string;
};

/** The leg on screen: settled first (the record), else the count from the live game, else its start. */
export function legLive(raw: ParlayLeg, game: ParlayGame | undefined, live: GameLive | undefined, now: number): LegLive {
  const leg = inferLeg(raw, game);
  if (typeof leg.res === "number") {
    return { state: leg.res === 1 ? "hit" : leg.res === 0 ? "miss" : "settled" };
  }
  const started = (live && live.state !== "pre") || (leg.start !== undefined && leg.start <= now);
  if (!live || live.state === "pre") {
    // A game with no ESPN link (most soccer, tennis challengers, cricket): no count, its result at settlement.
    if (started && !live) return { state: "live", note: "Under way · no live count" };
    return { state: started ? "live" : "pre", note: live?.detail };
  }
  const final = live.state === "post";
  const clock = final ? "Final" : live.detail;
  const no = leg.side === "no";
  const stat = baseStat(leg.stat);
  let count: number | undefined;
  let target: number | undefined;

  if (leg.kind === "prop" && leg.who) {
    const p = live.players[normName(leg.who)];
    if (stat === "double double" || stat === "triple double") {
      count = doubles(p?.stats);
      target = stat === "double double" ? 2 : 3;
    } else if (stat && typeof leg.line === "number") {
      count = statNow(stat, p?.stats);
      target = need(leg.line);
    }
  } else if (leg.kind === "game_line" && stat && game && !leg.inferred) {
    const score = (s: GameSide) => (leg.per ? periodScore(live[s]?.lines, leg.per, game.lg) : live[s]?.score);
    const mine = leg.sd ? score(leg.sd) : undefined;
    const theirs = leg.sd ? score(leg.sd === "home" ? "away" : "home") : undefined;
    if (stat === "total" && typeof leg.line === "number") {
      count = sum(score("home"), score("away"));
      target = need(leg.line);
    } else if (stat === "team_total" && typeof leg.line === "number") {
      count = mine;
      target = need(leg.line);
    } else if ((stat === "winner" || stat === "spread") && mine !== undefined && theirs !== undefined) {
      const by = mine - theirs;
      const needBy = stat === "spread" && typeof leg.line === "number" ? need(leg.line) : 1;
      const ok = by >= needBy;
      const lead = by > 0 ? `leads by ${by}` : by < 0 ? `trails by ${-by}` : "tied";
      const holds = no ? !ok : ok;
      if (final) return { state: holds ? "cleared" : "busted", note: `${clock} · ${by > 0 ? `won by ${by}` : by < 0 ? `lost by ${-by}` : "tied"}` };
      return { state: holds ? "live" : "danger", note: `${clock} · ${lead}` };
    }
  }

  if (count === undefined || target === undefined) {
    return { state: "live", note: clock };
  }
  if (!no) {
    if (count >= target) return { state: "cleared", now: count, target, note: `${clock} · cleared` };
    if (final) return { state: "busted", now: count, target, note: `${clock} · short by ${target - count}` };
    return { state: "live", now: count, target, note: `${clock} · needs ${target - count}` };
  }
  if (count >= target) return { state: "busted", now: count, target, under: true, note: `${clock} · busted` };
  if (final) return { state: "cleared", now: count, target, under: true, note: `${clock} · cleared` };
  const room = target - 1 - count;
  return {
    state: room === 0 ? "danger" : "live", now: count, target, under: true,
    note: `${clock} · ${room === 0 ? "one more busts it" : `room for ${room}`}`,
  };
}

// --------------------------------------------------------------------------- tickets and the day

export type TicketStage = "live" | "pre" | "done";

export function stage(t: ParlayTicket, now: number): TicketStage {
  if (t.outcome) return "done";
  return t.legs.some((l) => l.start !== undefined && l.start <= now) ? "live" : "pre";
}

/** Legs cleared (settled hits and live clears) and legs out (settled misses, live busts). */
export function tally(states: LegLive[]): { cleared: number; out: number } {
  return {
    cleared: states.filter((s) => s.state === "hit" || s.state === "cleared").length,
    out: states.filter((s) => s.state === "miss" || s.state === "busted").length,
  };
}

/** The first start among the legs not yet started. */
export function firstStart(t: ParlayTicket, now: number): number | undefined {
  const next = t.legs.map((l) => l.start).filter((s): s is number => s !== undefined && s > now);
  return next.length ? Math.min(...next) : undefined;
}

export function sortTickets(ts: ParlayTicket[]): ParlayTicket[] {
  return [...ts].sort((a, b) =>
    (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9)
    || (a.scope === b.scope ? 0 : a.scope === "cross_sport" ? 1 : -1)
    || b.at - a.at || a.id - b.id);
}

/** The empty slots worth a line: every result but "no eligible market" sports, which fold into one. */
export function emptySlots(results: SlotResult[]): { rows: SlotResult[]; noMarket: Map<string, string[]> } {
  const rows: SlotResult[] = [];
  const noMarket = new Map<string, string[]>();
  for (const r of results) {
    if (r.rule === "no eligible market") {
      const key = `${r.slot} ${r.tier}`;
      noMarket.set(key, [...(noMarket.get(key) ?? []), r.sport ?? "?"]);
    } else rows.push(r);
  }
  return { rows, noMarket };
}

// --------------------------------------------------------------------------- the record

export type RecordRow = {
  tier: string; scope: string; rv: string; sport?: string;
  tickets: number; open: number; settled: number; hit: number; partial: number;
  r_mean?: number; r_se?: number; days?: number;
  staked?: number; paid?: number; pl?: number;
  clv_n: number; clv_mean?: number; clv_pct_mean?: number; age_med?: number; age_max?: number; missed: number;
  empty?: Record<string, number>;
};

export type ParlayRecord = { asof: number; cohorts: string[]; rows: RecordRow[] };

export function cohortName(rv: string): string {
  return rv === "P2" ? "Cohort 1 · P2" : rv === "P2.1" ? "Cohort 2 · P2.1" : `Rules ${rv}`;
}
