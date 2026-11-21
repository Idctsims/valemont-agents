"use client";

import Link from "next/link";
import { useState } from "react";

import { TeamLogo } from "@/components/betting/matchup";
import type { Team } from "@/lib/betting/teams";
import { serviceFor } from "@/lib/gameday/services";
import {
  LEAGUES, followedLine, isLive, kindOf, leaderLine, liveClock, rankPrefix, statusLabel, timeSlot, tvItems,
  type SlateRow,
} from "@/lib/gameday/slate";
import { slateTeam } from "@/lib/gameday/teams";

// One event on Game Day as a scoreboard card (owner, Nov 20: "like the
// betting tab, with the logos"; then "a new way for the quarter and time
// left", "make me want to open the app"): each team's logo, name and rank,
// and the middle of the card is the scoreboard: the start time before the
// game, the period and clock in play under a pulsing LIVE, Final after. Each
// score sits in the board's box and pops when it changes. The networks run
// along the foot, a gold chip when on the owner's services. The whole card
// opens /gameday/[id]. No Kalshi price here.

/** One network: the gold chip when it streams on the owner's service (named in its title), else plain text. */
function Network({ name, service }: { name: string; service: string | null }) {
  if (!service) return <span data-testid="network">{name}</span>;
  return (
    <span
      data-testid="service-mark"
      data-service={service}
      title={`On your ${service}`}
      aria-label={`${name}, on your ${service}`}
      className="label-mono inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-pill bg-accent-strong px-2.5 text-on-accent-strong"
    >
      {name}
    </span>
  );
}

/** The networks line, shared by the card and the detail page. */
export function Networks({ tv, testId = "game-tv" }: { tv: readonly string[] | null | undefined; testId?: string }) {
  const items = tvItems(tv, serviceFor);
  return (
    <span data-testid={testId} className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {items.length === 0
        ? "Not listed"
        : items.map((it, i) => (
            <span key={it.name} className="inline-flex items-center gap-x-2">
              {i > 0 && <span aria-hidden className="text-text-muted">·</span>}
              <Network name={it.name} service={it.service} />
            </span>
          ))}
    </span>
  );
}

/** The pulsing red dot and LIVE, shared by the card and the section bar. */
export function LiveMark({ label = "Live" }: { label?: string }) {
  return (
    <span className="label-mono inline-flex items-center gap-1.5 text-live">
      <span aria-hidden className="relative inline-flex size-2">
        <span className="absolute inline-flex size-full animate-ping rounded-pill bg-live opacity-60" />
        <span className="relative inline-flex size-2 rounded-pill bg-live" />
      </span>
      {label}
    </span>
  );
}

function num(v: number | string | undefined): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** A side's score in the board's box. It pops when the live poll changes it; once final, the loser's is muted. */
function ScoreBox({ value, testId, muted }: { value: number | string | undefined; testId: string; muted: boolean }) {
  // The previous value kept in state (React's "information from previous renders"): no effect, no timer.
  const [prev, setPrev] = useState(value);
  const [bumps, setBumps] = useState(0);
  if (value !== prev) {
    setPrev(value);
    setBumps((n) => n + 1);
  }
  return (
    <span key={bumps}
          className={`mt-2.5 flex min-h-14 w-full max-w-38 items-center justify-center rounded-tile border border-border bg-bg ${
            bumps > 0 ? "animate-score-pop" : ""}`}>
      <span data-testid={testId}
            className={`font-mono text-3xl tabular-nums tracking-tight ${muted ? "text-text-muted" : "text-text"}`}>
        {value ?? ""}
      </span>
    </span>
  );
}

function Side({ team, rank, score, scoreId, muted, showScore }: {
  team: Team; rank: number | undefined; score: number | string | undefined; scoreId: string;
  muted: boolean; showScore: boolean;
}) {
  return (
    <div data-testid="matchup-team" data-home={team.home ? "1" : "0"}
         className={`flex min-w-0 flex-col items-center gap-0.5 text-center ${team.home ? "col-start-3" : ""}`}>
      <TeamLogo team={team} size="md" />
      <span data-testid="team-name" className="mt-1 max-w-full text-sm font-semibold text-text text-balance">
        {rankPrefix(rank) && <span className="font-mono text-xs font-normal text-text-muted">{rankPrefix(rank)}</span>}
        {team.city}
      </span>
      <span className="text-xs text-text-muted">{team.nick ? `${team.nick} · ` : ""}{team.home ? "home" : "away"}</span>
      {showScore && <ScoreBox value={score} testId={scoreId} muted={muted} />}
    </div>
  );
}

/** The scoreboard's middle: the start before, the period and clock in play, Final (or the status) after. */
function Middle({ row }: { row: SlateRow }) {
  const vs = row.neutral ? "vs" : "at";
  if (isLive(row)) {
    const c = liveClock(row);
    return (
      <div data-testid="game-clock" className="flex flex-col items-center gap-1 pt-3 text-center">
        <LiveMark />
        <span data-testid="game-period" className="font-mono text-xl font-semibold tabular-nums text-text">{c.period}</span>
        {c.clock && <span data-testid="game-time-left" className="font-mono text-sm tabular-nums text-live">{c.clock}</span>}
      </div>
    );
  }
  const status = statusLabel(row);
  if (status) {
    return (
      <div className="flex flex-col items-center gap-1 pt-5 text-center">
        <span data-testid="game-start" className="label-mono text-text">{status}</span>
        <span className="text-xs text-text-muted">{vs}</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-1 pt-4 text-center">
      <span data-testid="game-start" className="whitespace-nowrap font-mono text-base font-semibold tabular-nums text-text">
        {timeSlot(row)}
      </span>
      <span className="text-xs text-text-muted">{vs}</span>
    </div>
  );
}

export function GameRow({ row, className = "" }: { row: SlateRow; className?: string }) {
  const live = isLive(row);
  const kind = kindOf(row.league);
  const final = row.status === "final";
  const showScore = !!row.score && (live || final || row.status === "suspended");
  const tag = LEAGUES[row.league]?.short ?? row.league.toUpperCase();
  const teams = kind === "game" && row.home && row.away
    ? [slateTeam(row.tm?.[0], row.away, false), slateTeam(row.tm?.[1], row.home, true)]
    : null;
  const [sa, sh] = [num(row.score?.away), num(row.score?.home)];
  const decided = final && sa !== null && sh !== null && sa !== sh;

  return (
    <li data-testid="game-row" data-id={row.id} data-league={row.league} data-status={row.status}
        data-bama={row.bama ? "true" : undefined} className={`flex min-w-0 ${className}`}>
      <Link href={`/gameday/${row.id}`}
            className={`flex w-full min-w-0 flex-col overflow-hidden rounded-inner border bg-surface transition-colors hover:border-accent-ink ${
              live ? "border-live" : "border-border"}`}>
        <p className="flex min-h-9 items-center justify-between gap-3 px-4 pt-3 text-xs text-text-muted">
          <span className="label-mono">{tag}</span>
          <span className="flex items-center gap-2">
            {row.test && <span className="label-mono rounded-pill border border-border px-1.5">test</span>}
            {/* An event with no scoreboard middle keeps its slot up here: the round, the start, the status. */}
            {!teams && live && <LiveMark />}
            {!teams && <span data-testid="game-start" className="font-mono font-semibold text-text">{timeSlot(row)}</span>}
          </span>
        </p>

        {teams ? (
          <div className="grid grid-matchup items-start gap-2 px-4 pb-4 pt-1">
            <Side team={teams[0]} rank={row.ar} score={row.score?.away} scoreId="score-away"
                  muted={decided && sa! < sh!} showScore={showScore} />
            <div className="col-start-2 row-start-1 min-w-16">
              <Middle row={row} />
            </div>
            <Side team={teams[1]} rank={row.hr} score={row.score?.home} scoreId="score-home"
                  muted={decided && sh! < sa!} showScore={showScore} />
          </div>
        ) : (
          <div className="flex flex-col gap-1 px-4 pb-4 pt-1">
            <p className="font-display text-xl text-text">{row.title}</p>
            {/* A live TBD round already shows its round up top: once, not twice. */}
            {row.session && row.session !== timeSlot(row) && <p className="text-sm text-text-muted">{row.session}</p>}
            {showScore && row.score?.leader?.name && (
              <p data-testid="score-leader" className="mt-1 text-sm text-text">
                Leader: {kind === "golf_round" ? leaderLine(row.score.leader) : row.score.leader.name}
              </p>
            )}
            {showScore && kind === "golf_round" && (row.score?.followed ?? []).map((g) => (
              <p key={g.name} className="text-sm text-text-muted">{followedLine(g)}</p>
            ))}
            {showScore && row.score?.sets && (
              <p className="font-mono text-sm tabular-nums text-text">
                {row.score.sets.map((s) => s.join("–")).join("  ")}
              </p>
            )}
          </div>
        )}

        <span className="mt-auto flex min-h-12 items-center justify-between gap-3 border-t border-border bg-surface-2 px-4 py-2.5 text-sm text-text-muted">
          <Networks tv={row.tv} />
          <span aria-hidden className="text-accent-ink">→</span>
        </span>
      </Link>
    </li>
  );
}
