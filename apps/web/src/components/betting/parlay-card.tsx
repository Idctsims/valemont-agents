"use client";

import {
  Baseball, Basketball, BoxingGlove, FlagCheckered, Football, GameController, Golf, Hockey, PingPong, SoccerBall,
  TennisBall, Trophy, Volleyball, type Icon,
} from "@phosphor-icons/react";
import Link from "next/link";
import { useState } from "react";

import { TeamLogo } from "@/components/betting/matchup";
import {
  TIER_NAME, firstStart, inferLeg, legGame, legKey, legLive, legWords, multText, normName, signedUsd, stage, tally, usd,
  type GameLive, type GameTeam, type LegLive, type ParlayGame, type ParlayLeg, type ParlayTicket,
} from "@/lib/betting/parlays";
import { logoKey, logoUrls, type Team } from "@/lib/betting/teams";

// A Parlays ticket (Phase 4.5; docs/parlays_page.md §3): the tier, the
// multiplier and the paper stake up top, then each leg with its player's
// photo (or the teams' logos), in plain words, counting live like a
// PrizePicks lineup. No cents (owner, Nov 24). Paper: nothing here bets.

const CT = "America/Chicago";
const SHOW = 5;

/** Whole class names (Tailwind only generates what it can read). */
const TONE_TEXT = { hit: "text-hit", danger: "text-danger", "on-pace": "text-on-pace", "accent-ink": "text-text" } as const;
const TONE_BAR = { hit: "bg-hit", danger: "bg-danger", "on-pace": "bg-on-pace", "accent-ink": "bg-accent-ink" } as const;

export function clock(at: number): string {
  return new Date(at * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: CT });
}

const ctDay = (at: number) => new Date(at * 1000).toLocaleDateString("en-CA", { timeZone: CT });

/** A start: "9:00 PM" today, "Wed 2:00 PM" on another day. */
export function startText(at: number, now: number): string {
  if (ctDay(at) === ctDay(now)) return clock(at);
  return `${new Date(at * 1000).toLocaleDateString("en-US", { weekday: "short", timeZone: CT })} ${clock(at)}`;
}

function asTeam(t: GameTeam | undefined, home: boolean): Team | undefined {
  if (!t) return undefined;
  return { city: t.loc ?? t.name ?? t.abbr ?? "", ...(t.nick ? { nick: t.nick } : {}), ...logoUrls(logoKey(t.logo)), home };
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}

const SPORT_ICONS: Record<string, Icon> = {
  Basketball, Soccer: SoccerBall, Football, Hockey, Tennis: TennisBall, Baseball, Volleyball,
  "Table Tennis": PingPong, Golf, Esports: GameController, Boxing: BoxingGlove, MMA: BoxingGlove, Motorsport: FlagCheckered,
};

/** The leg's face: the player's photo ringed in his team's colour, else the two teams' logos, else the sport. */
function Face({ leg, game, img, sport }: { leg: ParlayLeg; game?: ParlayGame; img?: string; sport?: string }) {
  const color = leg.sd ? game?.[leg.sd]?.color : undefined;
  if (leg.kind === "prop" && leg.who) {
    return (
      <span aria-hidden data-testid="leg-face"
            className="relative inline-flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-pill bg-surface-2 ring-team"
            style={color && /^[0-9a-f]{6}$/i.test(color) ? ({ "--vm-team": `#${color}` } as React.CSSProperties) : undefined}>
        <span className="text-xs font-semibold text-text-muted">{initials(leg.who)}</span>
        {img && (
          // eslint-disable-next-line @next/next/no-img-element -- ESPN's CDN headshot, sized here
          <img src={img} alt="" loading="lazy" className="absolute inset-0 size-full object-cover object-top" />
        )}
      </span>
    );
  }
  const away = asTeam(game?.away, false);
  const home = asTeam(game?.home, true);
  if (away?.logo && home?.logo) {
    return (
      <span aria-hidden data-testid="leg-face" className="relative inline-block size-11 shrink-0">
        <span className="absolute left-0 top-0"><TeamLogo team={away} size="row" /></span>
        <span className="absolute bottom-0 right-0"><TeamLogo team={home} size="row" /></span>
      </span>
    );
  }
  const SportIcon = (sport && SPORT_ICONS[sport]) || Trophy;
  return (
    <span aria-hidden data-testid="leg-face"
          className="inline-flex size-11 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-text-muted">
      <SportIcon size={22} weight="duotone" />
    </span>
  );
}

const MARK: Record<string, { face: string; cls: string; label: string }> = {
  hit: { face: "✓", cls: "bg-hit-fill text-hit", label: "hit" },
  cleared: { face: "✓", cls: "bg-hit-fill text-hit", label: "cleared" },
  miss: { face: "✕", cls: "bg-danger-fill text-danger", label: "missed" },
  busted: { face: "✕", cls: "bg-danger-fill text-danger", label: "busted" },
};

function LegRow({ leg: raw, game, live, now, sport }: { leg: ParlayLeg; game?: ParlayGame; live?: GameLive; now: number; sport?: string }) {
  const leg = inferLeg(raw, game);
  const words = legWords(leg, game);
  const st: LegLive = legLive(leg, game, live, now);
  const img = leg.who ? live?.players[normName(leg.who)]?.img : undefined;
  const counting = st.now !== undefined && st.target !== undefined;
  const settled = st.state === "hit" || st.state === "miss" || st.state === "settled";
  const share = counting ? Math.min(1, st.now! / st.target!) : 0;
  const tone: keyof typeof TONE_TEXT = st.state === "cleared" || st.state === "hit" ? "hit"
    : st.state === "busted" || st.state === "miss" ? "danger" : st.state === "danger" ? "on-pace" : "accent-ink";
  const right = MARK[st.state] && (settled || !counting)
    ? <span role="img" aria-label={MARK[st.state].label}
            className={`inline-flex size-8 items-center justify-center rounded-pill font-semibold ${MARK[st.state].cls}`}>{MARK[st.state].face}</span>
    : counting
      ? <span className="text-right font-mono tabular-nums"><b className={`text-xl font-semibold ${TONE_TEXT[tone]}`}>{st.now}</b>
          <span className="text-sm text-text-muted">/{st.target}</span></span>
      : st.state === "pre" && leg.start ? <span className="font-mono text-sm text-text-muted">{startText(leg.start, now)}</span> : null;
  const body = (
    <>
      <Face leg={leg} game={game} img={img} sport={sport} />
      <span className="flex min-w-0 flex-col">
        <span className="font-semibold leading-snug text-text">{words.title}</span>
        {words.detail && <span className="text-sm leading-snug text-text-muted">{words.detail}</span>}
        {counting && !settled && (
          <span aria-hidden className="mt-1.5 block h-1.5 overflow-hidden rounded-pill bg-surface-3">
            <span className={`block h-full rounded-pill w-share ${TONE_BAR[tone]}`}
                  style={{ "--vm-share": share } as React.CSSProperties} />
          </span>
        )}
        {(st.note || (words.game && leg.kind === "prop")) && (
          <span className={`mt-1 text-xs ${st.state === "danger" ? "text-on-pace" : st.state === "cleared" ? "text-hit"
            : st.state === "busted" ? "text-danger" : "text-text-muted"}`}>
            {[leg.kind === "prop" ? words.game : undefined, st.note].filter(Boolean).join(" · ")}
          </span>
        )}
      </span>
      <span className="shrink-0">{right}</span>
    </>
  );
  const cls = "grid grid-cols-leg items-center gap-3 py-2.5";
  return (
    <li data-testid="parlay-leg" data-state={st.state} className="border-b border-border last:border-b-0">
      {leg.g
        ? <Link href={`/gameday/${leg.g}`} className={`${cls} hover:bg-surface-2`} aria-label={`${words.title}${words.detail ? `, ${words.detail}` : ""}: open the game`}>{body}</Link>
        : <div className={cls}>{body}</div>}
    </li>
  );
}

/** Legs cleared out of all, as a ring; red once a leg is out. */
function Ring({ cleared, total, out, pre }: { cleared: number; total: number; out: boolean; pre: boolean }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 44 44" className="size-11 shrink-0" role="img" aria-label={`${cleared} of ${total} legs cleared`}
         data-testid="parlay-ring">
      <circle cx="22" cy="22" r={r} fill="none" strokeWidth="4" className="stroke-surface-3" />
      {cleared > 0 && <circle cx="22" cy="22" r={r} fill="none" strokeWidth="4" strokeLinecap="round" transform="rotate(-90 22 22)"
              strokeDasharray={`${(c * cleared) / Math.max(total, 1)} ${c}`}
              className={out ? "stroke-danger" : pre ? "stroke-text-muted" : "stroke-hit"} />}
      <text x="22" y="26" textAnchor="middle" className="fill-text font-mono text-xs">{cleared}/{total}</text>
    </svg>
  );
}

export function TicketCard({ t, games, live, now, onDetails, selected }: {
  t: ParlayTicket;
  games: Record<string, ParlayGame>;
  live: Record<string, GameLive>;
  now: number;
  onDetails: (id: number) => void;
  selected?: boolean;
}) {
  const [all, setAll] = useState(false);
  const st = stage(t, now);
  const at = (l: ParlayTicket["legs"][number]) => { const k = legKey(l); return k ? live[k] : undefined; };
  const states = t.legs.map((l) => legLive(l, legGame(l, games, live), at(l), now));
  const { cleared, out } = tally(states);
  const liveGames = new Set(t.legs.filter((l, i) => legKey(l) && states[i].state !== "pre" && !l.res && at(l)?.state === "in")
    .map((l) => legKey(l))).size;
  const next = firstStart(t, now);
  const status = t.outcome === "hit" ? `paid ${usd(t.paid ?? t.pays)} · ${signedUsd((t.paid ?? t.pays) - t.stake)}`
    : t.outcome === "partial" ? `paid ${usd(t.paid ?? 0)} · ${signedUsd((t.paid ?? 0) - t.stake)}`
    : t.outcome === "miss" ? signedUsd(-t.stake)
    : st === "live" ? (out ? "a leg is out" : liveGames ? `${liveGames} game${liveGames === 1 ? "" : "s"} live` : "under way")
    : next ? `first game ${startText(next, now)}` : "";
  const legs = t.legs.length > SHOW + 1 && !all ? t.legs.slice(0, SHOW) : t.legs;
  const where = t.scope === "cross_sport" ? "Cross-sport" : t.sport ?? "";
  return (
    <article data-testid="parlay-ticket" data-id={t.id} data-tier={t.tier} data-stage={st} data-outcome={t.outcome ?? ""}
             aria-label={`${TIER_NAME[t.tier]} ${where} ticket, ${multText(t)}`}
             className={`rounded-card border bg-surface p-4 ${selected ? "border-accent-ink" : "border-border"} ${
               st === "live" && !out ? "border-live" : ""} ${t.outcome === "miss" ? "opacity-80" : ""}`}>
      <header className="flex items-center justify-between gap-3">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-text">{TIER_NAME[t.tier]}</span>
          <span className="text-sm text-text-muted">{where}</span>
          <span className="label-mono rounded-tile bg-paper-bg px-1.5 text-paper-fg">PAPER</span>
          {t.fill && <span className="label-mono rounded-tile border border-on-pace px-1.5 text-on-pace">{t.fill}</span>}
        </p>
        {t.outcome
          ? <span data-testid="parlay-result" className={`label-mono rounded-pill px-2.5 py-1 ${
              t.outcome === "miss" ? "bg-danger-fill text-danger" : "bg-hit-fill text-hit"}`}>
              {t.outcome === "hit" ? "Hit" : t.outcome === "partial" ? "Partial" : "Missed"}
            </span>
          : <Ring cleared={cleared} total={t.legs.length} out={out > 0} pre={st === "pre"} />}
      </header>
      <div className="mt-2 flex items-baseline gap-4">
        <span data-testid="parlay-mult" className="font-display text-4xl leading-none text-text">{multText(t)}</span>
        <span className="flex flex-col text-sm text-text-muted">
          <span><b data-testid="parlay-stake" className="font-semibold text-text">{usd(t.stake)}</b> to win{" "}
            <b className="font-semibold text-text">{usd(t.pays)}</b></span>
          <span>{t.legs.length} legs{status ? ` · ${status}` : ""}</span>
        </span>
      </div>
      <ul className="mt-3 border-t border-border">
        {legs.map((l, i) => (
          <LegRow key={i} leg={l} game={legGame(l, games, live)} live={at(l)} now={now}
                  sport={t.scope === "cross_sport" ? undefined : t.sport} />
        ))}
      </ul>
      {t.legs.length > SHOW + 1 && (
        <button type="button" onClick={() => setAll(!all)} data-testid="parlay-more"
                className="tap mt-1 text-sm font-semibold text-accent-ink">
          {all ? "Show fewer" : `+ ${t.legs.length - SHOW} more legs`}
        </button>
      )}
      <footer className="mt-1 flex items-center justify-between gap-3 text-sm text-text-muted">
        <span>{t.combo === "fits" ? "Fits as one Kalshi combo" : t.combo ? "Build it by hand on Kalshi" : ""}</span>
        <button type="button" onClick={() => onDetails(t.id)} data-testid="parlay-details-open"
                className="tap px-1 font-semibold text-accent-ink">Details</button>
      </footer>
    </article>
  );
}
