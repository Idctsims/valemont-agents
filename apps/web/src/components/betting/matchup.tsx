"use client";

import { useBetting } from "@/components/betting/betting-store";
import { pct } from "@/lib/betting/board";
import { legFrom, sideOf } from "@/lib/betting/combo";
import { priceText } from "@/lib/betting/price";
import type { Team } from "@/lib/betting/teams";

// The approved mock's matchup, shared by a board card and a game's header:
// each team's logo (ESPN's, light and dark), its name, away or home, and
// Kalshi's price to win as the big number in a box; then the consensus bar in
// the teams' own colours. A tap on a price adds its YES to the slip, as every
// price on /betting does; tapping one already in the slip takes it out.

/** ESPN's logo, the theme's variant; nothing when the game has no ESPN link. */
export function TeamLogo({ team, size }: { team: Team; size: "sm" | "row" | "md" | "lg" }) {
  if (!team.logo) return null;
  const box = size === "lg" ? "size-15 sm:size-19" : size === "md" ? "size-14" : size === "row" ? "size-8" : "size-5.5";
  return (
    <span aria-hidden className={`inline-block shrink-0 ${box}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- ESPN's CDN, sized here; no optimizer round trip */}
      <img src={team.logo} alt="" loading="lazy" className="logo-day size-full object-contain" />
      {/* eslint-disable-next-line @next/next/no-img-element -- as above */}
      <img src={team.logoDark ?? team.logo} alt="" loading="lazy" className="logo-night size-full object-contain" />
    </span>
  );
}

export type PricePick = {
  t: string; label: string; game: string; sport?: string; b?: number; a?: number; yesOnly: boolean; tm?: string;
  /** A claimed edge on its YES (db/078): the price is up. */
  claimed?: boolean;
};

/** The big price box: the number, then what it pays for ("TO WIN"). */
export function PriceBox({ pick, face = "to win", big = false, testId = "ml-pick" }: {
  pick: PricePick; face?: string; big?: boolean; testId?: string;
}) {
  const { unit, legs, tick } = useBetting();
  const side = sideOf(legs, pick.t);
  const on = side !== null;
  return (
    <button type="button" data-testid={testId} data-ticker={pick.t} data-side={side ?? undefined} aria-pressed={on}
            data-claimed={pick.claimed ? "1" : "0"}
            aria-label={`${pick.label}: ${priceText(pick.a, unit)}${side === "no" ? ", in the slip on NO" : on ? ", in the slip" : ""}`}
            onClick={() => tick(legFrom({ t: pick.t, label: pick.label, game: pick.game, sport: pick.sport, tm: pick.tm },
                                        side ?? "yes", pick, pick.yesOnly))}
            className={`mt-2 flex w-full flex-col items-center justify-center rounded-tile border transition-colors ${
              big ? "min-h-16 max-w-48" : "min-h-14 max-w-38"} ${
              on ? "border-accent bg-accent text-on-accent" : "border-border bg-bg text-text hover:border-accent-ink"}`}>
      <span data-testid="market-price"
            className={`font-mono tabular-nums tracking-tight ${big ? "text-3xl" : "text-2xl"} ${!on && pick.claimed ? "text-hit" : ""}`}>
        {priceText(pick.a, unit)}
      </span>
      <span className={`label-mono mt-1 ${on ? "" : "text-text-muted"}`}>{side === "no" ? `NO · ${face}` : face}</span>
    </button>
  );
}

/** Two teams face to face, each with its price box; a third price (a tie) beneath. */
export function Matchup({ teams, picks, big = false }: { teams: Team[]; picks: PricePick[]; big?: boolean }) {
  const [a, h] = teams;
  const tie = picks.slice(2);
  const name = (t: Team) => (big ? [t.city, t.nick].filter(Boolean).join(" ") : t.city);
  return (
    <>
      <div className={`grid grid-matchup items-start gap-2 px-4 ${big ? "pb-4 pt-6" : "pb-3.5 pt-4.5"}`}>
        {[a, h].map((t, i) => (
          <div key={i} data-testid="matchup-team" data-home={t.home ? "1" : "0"}
               className={`flex min-w-0 flex-col items-center gap-0.5 text-center ${i === 1 ? "col-start-3" : ""}`}>
            <TeamLogo team={t} size={big ? "lg" : "md"} />
            <span data-testid="team-name"
                  className={big ? "mt-2 font-display text-2xl text-text text-balance" : "mt-1 max-w-full truncate text-sm font-semibold text-text"}>
              {name(t)}
            </span>
            <span className="text-xs text-text-muted">
              {!big && t.nick ? `${t.nick} · ` : ""}{t.home ? "home" : "away"}
            </span>
            {picks[i] && <PriceBox pick={picks[i]} big={big} />}
          </div>
        ))}
        <span aria-hidden className={`col-start-2 row-start-1 text-xs text-text-muted ${big ? "pt-24" : "pt-16"}`}>at</span>
      </div>
      {tie.length > 0 && (
        <div className="flex justify-center gap-2 px-4 pb-3.5">
          {tie.map((p) => <div key={p.t} className="w-full max-w-38"><PriceBox pick={p} face={p.label.replace(/ to win$/, "")} /></div>)}
        </div>
      )}
    </>
  );
}

/** The consensus bar: each side's p over a bar in the teams' colours (the mock's), and whose number it is. */
export function ConsensusBar({ teams, p, source }: { teams: Team[]; p: (number | null)[]; source: string }) {
  const shown = p.slice(0, 2);
  return (
    <div data-testid="game-consensus" role="img" className="px-4 pb-3.5"
         aria-label={`Consensus: ${teams.slice(0, 2).map((t, i) => `${t.city} ${pct(shown[i])}`).join(", ")}`}>
      <p className="mb-1.5 flex items-baseline justify-between text-xs text-text-muted">
        <span data-testid="bar-away" className="font-mono text-sm text-text">{pct(shown[0])}</span>
        <span>{source}</span>
        <span data-testid="bar-home" className="font-mono text-sm text-text">{pct(shown[1])}</span>
      </p>
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-pill">
        {shown.map((v, i) => (
          <i key={i} data-testid="bar-team" className="block h-full grow-share bg-team"
             // No ESPN colour: the away side takes the accent, the home side the neutral step.
             style={{ "--vm-share": v ?? 0,
                      ...(teams[i]?.color ? { "--vm-team": teams[i].color } : i === 0 ? { "--vm-team": "var(--vm-accent)" } : {}),
                    } as React.CSSProperties} />
        ))}
      </div>
    </div>
  );
}
