import Link from "next/link";

import { TeamLogo } from "@/components/betting/matchup";
import { StatusChip } from "@/components/ui/badges";
import type { Team } from "@/lib/betting/teams";
import { serviceFor } from "@/lib/gameday/services";
import {
  LEAGUES, followedLine, isLive, kindOf, leaderLine, rankPrefix, statusLabel, timeSlot, tvItems, type SlateRow,
} from "@/lib/gameday/slate";
import { slateTeam } from "@/lib/gameday/teams";

// One event on Game Day as a card, in the betting board's look (owner, Nov
// 20: "like the betting tab, with the logos"): the start (or status) and the
// league; each team's logo, name and rank, and its score once live or over;
// a strip in the teams' own colours; and the networks along the foot, a gold
// chip when it is on the owner's services. The whole card links to
// /gameday/[id]. No Kalshi price here.

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

function num(v: number | string | undefined): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** A side's score in the board's price box; once final, the loser's in muted ink. */
function ScoreBox({ value, testId, muted }: { value: number | string | undefined; testId: string; muted: boolean }) {
  return (
    <span className="mt-2 flex min-h-14 w-full max-w-38 flex-col items-center justify-center rounded-tile border border-border bg-bg">
      <span data-testid={testId}
            className={`font-mono text-2xl tabular-nums tracking-tight ${muted ? "text-text-muted" : "text-text"}`}>
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

/** The teams' colours as one strip; a side with no colour takes the accent (away) or the neutral step (home). */
function ColourStrip({ teams }: { teams: Team[] }) {
  if (!teams.some((t) => t.color)) return null;
  return (
    <div aria-hidden data-testid="team-strip" className="flex h-1 gap-0.5">
      {teams.map((t, i) => (
        <i key={i} className="block h-full grow-share bg-team"
           style={(t.color ? { "--vm-team": t.color } : i === 0 ? { "--vm-team": "var(--vm-accent)" } : {}) as React.CSSProperties} />
      ))}
    </div>
  );
}

export function GameRow({ row }: { row: SlateRow }) {
  const live = isLive(row);
  const status = statusLabel(row);
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
        data-bama={row.bama ? "true" : undefined} className="flex min-w-0">
      <Link href={`/gameday/${row.id}`}
            className={`flex w-full min-w-0 flex-col overflow-hidden rounded-inner border bg-surface transition-colors hover:border-accent-ink ${
              live ? "border-accent-ink" : "border-border"}`}>
        {teams && <ColourStrip teams={teams} />}
        <p className="flex min-h-10 items-center justify-between gap-3 border-b border-border px-4 py-2 text-xs text-text-muted">
          <span data-testid="game-start" className="font-semibold text-text">{timeSlot(row)}</span>
          <span className="flex items-center gap-2">
            {live && <StatusChip status="live" label={status ?? "Live"} />}
            {row.test && <span className="label-mono rounded-pill border border-border px-1.5">test</span>}
            <span>{tag}</span>
          </span>
        </p>

        {teams ? (
          <div className="grid grid-matchup items-start gap-2 px-4 pb-3.5 pt-4">
            <Side team={teams[0]} rank={row.ar} score={row.score?.away} scoreId="score-away"
                  muted={decided && sa! < sh!} showScore={showScore} />
            <span aria-hidden className="col-start-2 row-start-1 pt-5 text-xs text-text-muted">
              {row.neutral ? "vs" : "at"}
            </span>
            <Side team={teams[1]} rank={row.hr} score={row.score?.home} scoreId="score-home"
                  muted={decided && sh! < sa!} showScore={showScore} />
          </div>
        ) : (
          <div className="flex flex-col gap-1 px-4 pb-3.5 pt-4">
            <p className="font-display text-xl text-text">{row.title}</p>
            {/* A live TBD round already shows its round in the time slot: once, not twice. */}
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
