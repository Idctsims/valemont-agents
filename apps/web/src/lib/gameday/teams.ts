// Game Day's team faces (owner, Nov 20: "like the betting tab, with the
// logos"): ESPN's city, nickname, logo and colour, as the worker already
// stores them in sports_events.participants (the worker's ESPN parse, Nov 15). Pure: no I/O.

import { logoKey, logoUrls, type Team } from "@/lib/betting/teams";

/** One side as the slate carries it: the raw ESPN fields, any of them absent. */
export type SlateSide = { loc?: string; nick?: string; logo?: string; color?: string };

/** The narrow read of a game's two sides (data.ts selects these JSON paths only). */
export type TeamRow = {
  id: number;
  aloc: string | null; an: string | null; al: string | null; ac: string | null;
  hloc: string | null; hn: string | null; hl: string | null; hc: string | null;
};

const HEX = /^[0-9a-f]{6}$/i;

function clean(v: string | null | undefined): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function side(loc: string | null, nick: string | null, logo: string | null, color: string | null): SlateSide {
  const out: SlateSide = {};
  const l = clean(loc);
  const n = clean(nick);
  const k = logoKey(logo);
  const c = clean(color);
  if (l) out.loc = l;
  if (n) out.nick = n;
  if (k) out.logo = k;
  if (c && HEX.test(c)) out.color = c.toLowerCase();
  return out;
}

/** A row of the narrow read as [away, home], or nothing when ESPN gave neither side anything. */
export function slateSides(r: TeamRow): [SlateSide, SlateSide] | null {
  const a = side(r.aloc, r.an, r.al, r.ac);
  const h = side(r.hloc, r.hn, r.hl, r.hc);
  return Object.keys(a).length || Object.keys(h).length ? [a, h] : null;
}

/**
 * The card's face for one side: the city big and the nickname under it when
 * ESPN splits them, else the full name as the slate writes it; the logo and
 * colour when ESPN has them.
 */
export function slateTeam(s: SlateSide | undefined, full: string, home: boolean): Team {
  const split = !!s?.loc && !!s?.nick && s.nick !== s.loc && full === `${s.loc} ${s.nick}`;
  return {
    city: split ? s!.loc! : full,
    ...(split ? { nick: s!.nick } : {}),
    ...logoUrls(s?.logo),
    ...(s?.color ? { color: `#${s.color}` } : {}),
    home,
  };
}
