// The board card's teams, logos, consensus bar and main lines, as the approved
// mock draws them (owner, Nov 15: "match the mock on all of them"; db/083).
// No page, no server: what kalshi_board_cards returns, into what a card shows.

import type { BoardCard } from "./board";

/** One side of a game, in the order the card shows it (Kalshi's: away first). */
export type Team = {
  /** The big name: ESPN's city ("Milwaukee"), else the side as the title writes it. */
  city: string;
  /** The nickname under it ("Bucks"), when ESPN gives one. */
  nick?: string;
  logo?: string;
  logoDark?: string;
  /** The team's colour for the consensus bar: a CSS colour from ESPN's hex. */
  color?: string;
  /** Kalshi's code for the side's winner market (its ticker suffix). */
  code?: string;
  home: boolean;
};

const LOGO_HOST = "https://a.espncdn.com/i/teamlogos";
/** ESPN keeps a dark-ground variant for these folders only. */
const DARK_FOLDERS = new Set(["nba", "nfl", "nhl", "mlb", "wnba"]);
const LOGO_KEY = /^([a-z]+)\/([a-z0-9-]+)$/;
const HEX = /^[0-9a-f]{6}$/;

/** A logo key ("nba/det", "ncaa/254") as ESPN's URLs: light, and dark where ESPN has one. */
export function logoUrls(key: string | null | undefined): { logo?: string; logoDark?: string } {
  const hit = key ? LOGO_KEY.exec(key) : null;
  if (!hit) return {};
  const [, folder, file] = hit;
  const logo = `${LOGO_HOST}/${folder}/500/${file}.png`;
  return { logo, logoDark: DARK_FOLDERS.has(folder) ? `${LOGO_HOST}/${folder}/500-dark/${file}.png` : logo };
}

type Participant = { name?: unknown; loc?: unknown; nick?: unknown; logo?: unknown; color?: unknown };
type TeamTuple = [string, string | null, string | null, string | null];

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

/** ESPN's logo path folded to db/083's key: "nba/500/scoreboard/det.png" -> "nba/det". */
export function logoKey(path: unknown): string | null {
  const hit = typeof path === "string" ? /^([a-z]+)\/500\/(?:scoreboard\/)?([a-z0-9-]+)\.png$/.exec(path) : null;
  return hit ? `${hit[1]}/${hit[2]}` : null;
}

/** A linked ESPN game's teams as db/083's card carries them (tm, hs): the game page reads the same row. */
export function teamTuples(
  row: { participants: unknown; home: string | null; away: string | null; home_away_swapped: boolean | null },
): { tm: [TeamTuple, TeamTuple]; hs?: boolean } | null {
  const p = row.participants;
  if (!p || typeof p !== "object" || Array.isArray(p)) return null;
  const side = (s: Participant | undefined, name: string | null): TeamTuple =>
    [str(s?.loc) ?? name ?? "", str(s?.nick), logoKey(s?.logo), str(s?.color)];
  const o = p as { away?: Participant; home?: Participant };
  const a = side(o.away, row.away);
  const h = side(o.home, row.home);
  return row.home_away_swapped ? { tm: [h, a], hs: true } : { tm: [a, h] };
}

/** "A at B", "A vs B", "A @ B": the two sides as written, whole. */
export function titleSides(title: string): [string, string] | null {
  const hit = /^(.+?)\s+(?:at|vs\.?|@)\s+(.+)$/i.exec(title.trim());
  return hit ? [hit[1].trim(), hit[2].trim()] : null;
}

/** The card's two sides, in Kalshi's order (the winner prices' order): from the linked ESPN game when
 *  there is one, else from the title. Each carries its winner market's Kalshi code. */
export function cardTeams(c: Pick<BoardCard, "tm" | "hs" | "t" | "w">): Team[] | null {
  const codes = (c.w ?? []).map((w) => w[0]);
  if (c.tm && c.tm.length === 2) {
    return c.tm.map((t, i) => {
      const [city, nick, key, color] = t;
      const home = c.hs ? i === 0 : i === 1;
      return {
        city: city || codes[i] || "",
        // Soccer sends the club's name as both city and nickname: say it once.
        ...(nick && nick !== city ? { nick } : {}),
        ...logoUrls(key),
        ...(color && HEX.test(color) ? { color: `#${color}` } : {}),
        ...(codes[i] ? { code: codes[i] } : {}),
        home,
      };
    });
  }
  const sides = c.t ? titleSides(c.t) : null;
  if (!sides) return null;
  return sides.map((s, i) => ({ city: s, ...(codes[i] ? { code: codes[i] } : {}), home: i === 1 }));
}

/** The card's title: Kalshi's, else the teams' "City Nick at City Nick". */
export function cardTitle(c: Pick<BoardCard, "tm" | "hs" | "t" | "w" | "g">): string {
  if (c.t) return c.t;
  const teams = cardTeams(c);
  if (!teams) return c.g;
  const name = (t: Team) => [t.city, t.nick].filter(Boolean).join(" ");
  const [away, home] = teams[0].home ? [teams[1], teams[0]] : [teams[0], teams[1]];
  return `${name(away)} at ${name(home)}`;
}

/** The consensus bar: each side's p in Kalshi's order, and whose number it is. */
export function cardBar(c: Pick<BoardCard, "w" | "wn" | "we">): { p: (number | null)[]; source: string } | null {
  const w = c.w ?? [];
  if (w.length < 2 || w.every((x) => typeof x[3] !== "number")) return null;
  const source = c.wn ? `${c.wn} books say` : c.we ? "ESPN's book says" : "Kalshi mid";
  return { p: w.map((x) => (typeof x[3] === "number" ? x[3] : null)), source };
}

/** A main-line ticker from the card: '~' stands for the game's date-and-teams part (db/083). */
export function expandTicker(t: string, game: string): string {
  if (!t.includes("~")) return t;
  const tail = game.slice(game.indexOf("-") + 1);
  return t.replace("~", tail);
}

function num(v: number): string {
  return Number.isInteger(v) ? v.toFixed(0) : String(v);
}

export type MainLine = { t: string; label: string; b?: number; a?: number; yesOnly: boolean };

/** The card's Spread and Total cells: "Orlando −8.5", "Over 224.5", each with its market to tap. */
export function cardLines(c: Pick<BoardCard, "g" | "sp" | "to" | "tm" | "hs" | "t" | "w">): { spread?: MainLine; total?: MainLine } {
  const out: { spread?: MainLine; total?: MainLine } = {};
  const price = (v: number | null) => (typeof v === "number" ? v : undefined);
  if (c.sp) {
    const [t, who, line, b, a, yo] = c.sp;
    const team = cardTeams(c)?.find((x) => x.code === who);
    out.spread = { t: expandTicker(t, c.g), label: `${team?.city ?? who ?? ""} −${num(line)}`.trim(), b: price(b), a: price(a),
                   yesOnly: yo === 1 };
  }
  if (c.to) {
    const [t, line, b, a, yo] = c.to;
    out.total = { t: expandTicker(t, c.g), label: `Over ${num(line)}`, b: price(b), a: price(a), yesOnly: yo === 1 };
  }
  return out;
}
