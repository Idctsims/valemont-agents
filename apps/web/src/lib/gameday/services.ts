// THE map from ESPN broadcast names to the owner's streaming services
// (Netflix, HBO Max, Paramount+, Showtime, Starz, Prime Video). Game Day's
// gold mark and Wags' "where can I watch X" both read this one file; the
// database never knows it. Built from the 97 names stored in sports_events on
// 2026-11-05 (docs/gameday.md §3). Keys are compared upper-case, trimmed.
//
// To flip a judgment call, change its one line: add or remove the entry.

export type Service = "Netflix" | "HBO Max" | "Paramount+" | "Showtime" | "Starz" | "Prime Video";

export const BROADCAST_SERVICES: Readonly<Record<string, Service>> = {
  // ---- exact: the service is the broadcaster
  NETFLIX: "Netflix",
  "HBO MAX": "HBO Max",
  MAX: "HBO Max",
  "PARAMOUNT+": "Paramount+",
  "PARAMOUNT PLUS": "Paramount+",
  SHOWTIME: "Showtime",
  STARZ: "Starz",
  "PRIME VIDEO": "Prime Video",
  "AMAZON PRIME VIDEO": "Prime Video",
  PRIME: "Prime Video",

  // ---- judgment calls (owner, 2026-11-05)
  // YES: Paramount+ streams the local CBS feed live (NFL and CFB on CBS).
  CBS: "Paramount+",
  // YES: HBO Max carries TNT Sports' live events (NHL, college basketball, CFP).
  TNT: "HBO Max",
  // YES: truTV is TNT Sports too (NCAAB, NHL).
  TRUTV: "HBO Max",
  // YES: TBS is TNT Sports too (none stored yet; MLB and NCAAB later).
  TBS: "HBO Max",

  // NO (owner): CBSSN, CBS Sports Network: not on the owner's Paramount+ tier.
  //   To flip: CBSSN: "Paramount+",
  // NO (owner): "Prime Video (Local)", NHL regional feeds, in-market only.
  //   To flip: "PRIME VIDEO (LOCAL)": "Prime Video",
};

/** The owner's service one broadcaster streams on, or null. */
export function serviceFor(name: string): Service | null {
  return BROADCAST_SERVICES[name.trim().toUpperCase()] ?? null;
}

/** The owner's services a broadcast list streams on, once each, in ESPN's order. */
export function servicesFor(broadcast: readonly string[] | null | undefined): Service[] {
  const out: Service[] = [];
  for (const name of broadcast ?? []) {
    const s = BROADCAST_SERVICES[name.trim().toUpperCase()];
    if (s && !out.includes(s)) out.push(s);
  }
  return out;
}

/**
 * Wags' context (db/068 ctx_sports_today) with `streams_on` added to every
 * game that streams on one of the owner's services, so "where can I watch X"
 * is answered from this map, not guessed. Returns a new object; the recorded
 * snapshot keeps ESPN's names only.
 */
export function annotateSportsContext(context: Record<string, unknown>): Record<string, unknown> {
  const sports = context.sports_today;
  if (!sports || typeof sports !== "object" || Array.isArray(sports)) return context;
  const out: Record<string, unknown> = { ...(sports as Record<string, unknown>) };
  for (const key of ["live", "owner_five", "other_tonight", "this_week", "alabama_next"]) {
    const list = out[key];
    if (!Array.isArray(list)) continue;
    out[key] = list.map((g) => {
      if (!g || typeof g !== "object") return g;
      const tv = (g as { tv?: unknown }).tv;
      const on = servicesFor(Array.isArray(tv) ? tv.filter((x): x is string => typeof x === "string") : []);
      return on.length ? { ...(g as object), streams_on: on } : g;
    });
  }
  out.about = `${typeof out.about === "string" ? `${out.about} ` : ""}streams_on: the owner's own streaming services that carry the game (from the broadcaster).`;
  return { ...context, sports_today: out };
}
