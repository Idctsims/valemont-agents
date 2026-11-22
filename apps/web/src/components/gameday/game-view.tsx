"use client";

import { ArrowUpRight, Television } from "@phosphor-icons/react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { pollLive, pollSummary } from "@/app/(app)/gameday/actions";
import { TeamLogo } from "@/components/betting/matchup";
import { LiveMark, Networks } from "@/components/gameday/game-row";
import type { Team } from "@/lib/betting/teams";
import {
  POLL_SECONDS, applyPatches, dateLabel, isLive, kalshiUrl, liveClock, startLabel, statusLabel, type SlateRow,
} from "@/lib/gameday/slate";
import type { GameSummary, Play, Side, StatGroup } from "@/lib/gameday/summary";

// One team game, laid out like Google's game result (owner, Nov 21, with
// five Google screenshots: "keep the Kalshi shortcut but match everything
// else to how Google does it"): the day's other games in a strip, the league
// and week, the two teams with big plain scores and the clock between them,
// football's down and distance on a field, the last play, pill tabs
// (Overview, Player stats, Team stats, Play-by-play), and on Overview the
// TV and Kalshi buttons, the score by period, the leaders, the scoring
// summary and the venue. ESPN's summary is read on open and every
// SUMMARY_SECONDS while the game is on (espn.ts); with none (a test row,
// ESPN down) the header falls back to the database's score and poll.

const SUMMARY_SECONDS = 30;

export type KalshiInfo = { state: string | null; reason: string | null; ticker: string | null; series: string | null };
export type StripGame = { id: number; away: Team; home: Team; row: SlateRow };

type Tab = "overview" | "players" | "team" | "plays";

/** The database row with ESPN's fresher score and status laid over it. */
function merged(row: SlateRow, s: GameSummary | null): SlateRow {
  if (!s) return row;
  const status = s.state === "in" ? (row.status === "delayed" ? "delayed" : "live")
    : s.state === "post" ? "final" : row.status;
  const scored = s.state === "in" || s.state === "post";
  return {
    ...row,
    status,
    detail: s.state === "pre" ? row.detail : (s.detail ?? row.detail),
    score: scored ? { ...row.score, away: s.away.score ?? row.score?.away, home: s.home.score ?? row.score?.home } : row.score,
  };
}

function relDay(day: string, today: string): string {
  const d = (Date.parse(`${day}T12:00:00Z`) - Date.parse(`${today}T12:00:00Z`)) / 86_400_000;
  if (d === 0) return "Today";
  if (d === -1) return "Yesterday";
  if (d === 1) return "Tomorrow";
  return dateLabel(day);
}

function recordText(r: string | undefined): string | null {
  return r ? `(${r.replace(/-/g, " - ")})` : null;
}

function num(v: number | string | undefined): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** A score that pops when it changes (the card's animation), in big light digits. */
function BigScore({ value, testId, muted }: { value: number | string | undefined; testId: string; muted: boolean }) {
  const [prev, setPrev] = useState(value);
  const [bumps, setBumps] = useState(0);
  if (value !== prev) {
    setPrev(value);
    setBumps((n) => n + 1);
  }
  return (
    <span key={bumps} data-testid={testId}
          className={`font-sans text-4xl font-light leading-none tabular-nums tracking-tight ${
            muted ? "text-text-muted" : "text-text"} ${bumps > 0 ? "animate-score-pop rounded-tile" : ""}`}>
      {value ?? ""}
    </span>
  );
}

/** One side's name, rank and record under its logo and score (Google: "13 Oregon", "(8 - 3)"). */
function TeamName({ name, rank, record, side, possession }: {
  name: string; rank?: number; record?: string; side: Side; possession?: boolean;
}) {
  const right = side === "home";
  return (
    <div className={`flex min-w-0 flex-col gap-0.5 ${right ? "items-end text-right" : "items-start"}`}>
      <span data-testid="team-name" className="flex max-w-full items-center gap-1.5 text-lg leading-tight text-text">
        {possession && right && <Football />}
        <span className="text-balance">
          {rank != null && <span className="mr-1 text-text-muted">{rank}</span>}
          {name}
        </span>
        {possession && !right && <Football />}
      </span>
      {recordText(record) && <span className="text-sm tabular-nums text-text-muted">{recordText(record)}</span>}
    </div>
  );
}

function Football() {
  return <span aria-label="Has the ball" role="img" className="inline-block h-2.5 w-4 shrink-0 rounded-pill bg-wood" />;
}

function Middle({ row, today }: { row: SlateRow; today: string }) {
  if (isLive(row)) {
    const c = liveClock(row);
    return (
      <div data-testid="game-clock" className="flex flex-col items-center gap-1 text-center">
        <span className="whitespace-nowrap font-sans text-lg tabular-nums text-hit">
          <span data-testid="game-period">{c.period}</span>
          {c.clock && <> - <span data-testid="game-time-left">{c.clock}</span></>}
        </span>
        <span aria-hidden className="h-0.5 w-10 animate-pulse rounded-pill bg-hit" />
      </div>
    );
  }
  const status = statusLabel(row);
  return (
    <div className="flex flex-col items-center text-center text-base leading-snug text-text">
      <span data-testid="event-when">{status ?? relDay(row.day, today)}</span>
      <span className="text-text-muted">{status ? relDay(row.day, today) : startLabel(row)}</span>
    </div>
  );
}

/** The field: each end zone in its team's colour, the ball at its spot, the line to gain. */
function Field({ s, away, home, abbrs }: {
  s: NonNullable<GameSummary["situation"]>; away: Team; home: Team; abbrs: [string, string];
}) {
  const pos = (x: number) => `${10 + x * 0.8}%`;
  return (
    <div data-testid="game-field" className="mt-5">
      <p data-testid="game-down" className="text-center text-sm text-text">{s.text}</p>
      <div className="relative mt-3 h-6" role="img" aria-label={`Ball on ${s.text.split(" at ")[1] ?? "the field"}`}>
        <div className="absolute inset-0 flex overflow-hidden rounded-tile bg-band">
          <i className="block h-full w-1/10 bg-team opacity-80"
             style={{ "--vm-team": away.color ?? "var(--vm-accent)" } as React.CSSProperties} />
          <span className="flex-1" />
          <i className="block h-full w-1/10 bg-team opacity-80"
             style={{ "--vm-team": home.color ?? "var(--vm-text-muted)" } as React.CSSProperties} />
        </div>
        {[30, 50, 70].map((m) => (
          <i key={m} aria-hidden className="absolute inset-y-1 w-px bg-border" style={{ left: `${m}%` }} />
        ))}
        {s.toGo != null && (
          <i aria-hidden className="absolute -inset-y-0.5 w-0.5 rounded-pill bg-accent-strong" style={{ left: pos(s.toGo) }} />
        )}
        <i aria-hidden className="absolute top-1/2 h-3 w-5 -translate-x-1/2 -translate-y-1/2 rounded-pill border border-text bg-wood"
           style={{ left: pos(s.x) }} />
      </div>
      <p className="mt-1.5 flex justify-between text-xs text-text-muted">
        <span>{abbrs[0]}</span><span>20</span><span>50</span><span>20</span><span>{abbrs[1]}</span>
      </p>
    </div>
  );
}

function LastPlay({ play, team }: { play: Play; team?: Team }) {
  return (
    <div data-testid="last-play" className="mt-4 flex gap-3 rounded-inner bg-band p-3">
      {team?.logo && <span className="mt-0.5"><TeamLogo team={team} size="sm" /></span>}
      <p className="text-sm text-text">
        {play.clock && <span className="tabular-nums text-text-muted">{play.clock} · </span>}
        {play.text}
      </p>
    </div>
  );
}

function Pill({ on, children, onClick, testId }: { on: boolean; children: React.ReactNode; onClick: () => void; testId: string }) {
  return (
    <button type="button" role="tab" aria-selected={on} data-testid={testId} onClick={onClick}
            className={`tap shrink-0 whitespace-nowrap rounded-pill border px-4 text-base transition-colors ${
              on ? "border-text bg-band text-text" : "border-transparent bg-band text-text-muted hover:text-text"}`}>
      {children}
    </button>
  );
}

function KalshiButton({ k }: { k: KalshiInfo }) {
  if (k.state === "linked" && k.ticker && k.series) {
    return (
      <a data-testid="kalshi-link" href={kalshiUrl(k.series, k.ticker)} target="_blank" rel="noopener noreferrer"
         title={k.ticker}
         className="tap flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-pill border border-border px-3 text-base text-accent-ink hover:border-accent-ink">
        Open on Kalshi <ArrowUpRight aria-hidden size={16} />
      </a>
    );
  }
  const label = k.state === "not_listed" ? "Not listed yet" : k.state === "not_linked" || k.state === "out_of_scope" ? "Not linked" : "Not checked yet";
  return (
    <div className="flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-pill border border-border px-4 text-center text-sm text-text-muted">
      <span aria-hidden>Kalshi ·</span>
      <span data-testid="kalshi-state" data-state={k.state ?? "unchecked"}>{label}</span>
      {k.reason && <span data-testid="kalshi-reason" className="sr-only">{k.reason}</span>}
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return <h2 className="text-xl text-text">{children}</h2>;
}

function LineScore({ s }: { s: GameSummary }) {
  const cell = (v: string | undefined) => <td className="w-9 py-1.5 text-center tabular-nums">{v ?? "-"}</td>;
  return (
    <table data-testid="line-score" className="w-full text-base">
      <thead className="text-text-muted">
        <tr>
          <th scope="col" className="py-1.5 text-left font-normal">Team</th>
          {s.periods.map((p) => <th key={p} scope="col" className="w-9 py-1.5 text-center font-normal">{p}</th>)}
          <th scope="col" className="w-11 py-1.5 text-center font-normal">T</th>
        </tr>
      </thead>
      <tbody className="text-text">
        {(["away", "home"] as const).map((side) => (
          <tr key={side}>
            <th scope="row" className="py-1.5 text-left font-normal">{s[side].abbr}</th>
            {s.periods.map((_, i) => <td key={i} className="w-9 py-1.5 text-center tabular-nums">{s[side].lines[i] ?? "-"}</td>)}
            {cell(s[side].score)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Leaders({ s, teams }: { s: GameSummary; teams: Record<Side, Team> }) {
  const cats = [...new Set(s.leaders.map((l) => l.category))];
  if (!cats.length) return null;
  return (
    <section data-testid="game-leaders" className="mt-8">
      <Heading>Game leaders</Heading>
      <ul className="mt-3 divide-y divide-border">
        {cats.map((c) => (
          <li key={c} className="py-3">
            <p className="text-sm text-text-muted">{c}</p>
            <div className="mt-1.5 grid grid-cols-2 gap-4">
              {(["away", "home"] as const).map((side) => {
                const l = s.leaders.find((x) => x.category === c && x.side === side);
                return (
                  <div key={side} className={`flex min-w-0 items-start gap-2 ${side === "home" ? "flex-row-reverse text-right" : ""}`}>
                    {teams[side].logo && <TeamLogo team={teams[side]} size="sm" />}
                    {l ? (
                      <span className="min-w-0">
                        <span className="block truncate text-base text-text">{l.name}</span>
                        <span className="block text-sm tabular-nums text-text-muted">{l.line}</span>
                      </span>
                    ) : <span className="text-sm text-text-muted">—</span>}
                  </div>
                );
              })}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PlayList({ plays, s, teams, testId }: { plays: Play[]; s: GameSummary; teams: Record<Side, Team>; testId: string }) {
  return (
    <ul data-testid={testId} className="mt-3">
      {plays.map((p, i) => {
        // A period's heading over its first play (the list is in order: newest first, or game order).
        const head = p.period && p.period !== plays[i - 1]?.period ? p.period : null;
        return (
          <li key={i}>
            {head && (
              <p className="mt-4 flex justify-between border-b border-border pb-1.5 text-sm text-text-muted">
                <span>{head}</span>
                <span className="flex gap-4"><span className="w-9 text-center">{s.away.abbr}</span><span className="w-9 text-center">{s.home.abbr}</span></span>
              </p>
            )}
            <div className="flex gap-3 border-b border-border py-2.5">
              <span className="w-5.5 shrink-0">{p.side && teams[p.side].logo && <TeamLogo team={teams[p.side]} size="sm" />}</span>
              <div className="min-w-0 flex-1">
                {p.type && p.scoring && <p className="text-sm text-text">{p.type}{p.clock ? ` · ${p.clock}` : ""}</p>}
                <p className={`text-sm ${p.scoring ? "text-text-muted" : "text-text"}`}>
                  {!p.scoring && p.clock && <span className="tabular-nums text-text-muted">{p.clock} · </span>}
                  {p.text}
                </p>
              </div>
              {p.away != null && p.home != null && (p.scoring || testId === "scoring-summary") ? (
                <span className="flex shrink-0 gap-4 text-base tabular-nums text-text">
                  <span className="w-9 text-center">{p.away}</span><span className="w-9 text-center">{p.home}</span>
                </span>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function PlayerStats({ s, teams }: { s: GameSummary; teams: Record<Side, Team> }) {
  const [side, setSide] = useState<Side>("away");
  const groups: StatGroup[] = s.players[side];
  return (
    <div data-testid="player-stats">
      <div role="tablist" className="grid grid-cols-2 border-b border-border">
        {(["away", "home"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={side === t} onClick={() => setSide(t)}
                  className={`tap -mb-px border-b-2 text-base ${side === t ? "border-text text-text" : "border-transparent text-text-muted"}`}>
            {teams[t].nick && teams[t].city.length > 14 ? teams[t].nick : teams[t].city}
          </button>
        ))}
      </div>
      {groups.length === 0 && <p className="mt-6 text-base text-text-muted">No player stats yet.</p>}
      {groups.map((g) => (
        <section key={g.name} className="mt-6">
          <Heading>{g.name}</Heading>
          <div className="scroll-row mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-text-muted">
                <tr className="border-b border-border">
                  <th scope="col" className="sticky left-0 bg-bg py-2 pr-3 text-left font-normal">Player</th>
                  {g.labels.map((l) => <th key={l} scope="col" className="px-1.5 py-2 text-right font-normal">{l}</th>)}
                </tr>
              </thead>
              <tbody className="text-text">
                {g.rows.map((r, i) => (
                  <tr key={i} className="border-b border-border">
                    <th scope="row" className="sticky left-0 whitespace-nowrap bg-bg py-2.5 pr-2 text-left text-base font-normal">
                      {r.jersey && <span className="mr-2 inline-block w-5 text-right tabular-nums text-text-muted">{r.jersey}</span>}
                      {r.name}
                    </th>
                    {r.stats.map((v, j) => <td key={j} className="whitespace-nowrap px-1.5 py-2.5 text-right tabular-nums">{v}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function TeamStats({ s, teams }: { s: GameSummary; teams: Record<Side, Team> }) {
  return (
    <div data-testid="team-stats">
      <div className="flex items-center justify-between border-b border-border pb-3">
        {teams.away.logo ? <TeamLogo team={teams.away} size="sm" /> : <span>{s.away.abbr}</span>}
        <span className="text-sm text-text-muted">Team stats</span>
        {teams.home.logo ? <TeamLogo team={teams.home} size="sm" /> : <span>{s.home.abbr}</span>}
      </div>
      <ul>
        {s.teamStats.map((t) => (
          <li key={t.label} className="grid grid-cols-3 border-b border-border py-2.5 text-base">
            <span className="tabular-nums text-text">{t.away}</span>
            <span className="text-center text-sm text-text-muted">{t.label}</span>
            <span className="text-right tabular-nums text-text">{t.home}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function GameView({
  initial, initialSummary, teams, names, ranks, leagueLabel, today, kalshi, venue, neutral, tv, strip, children,
}: {
  initial: SlateRow;
  initialSummary: GameSummary | null;
  teams: Record<Side, Team>;
  names: Record<Side, string>;
  ranks: Partial<Record<Side, number>>;
  leagueLabel: string;
  today: string;
  kalshi: KalshiInfo;
  venue: string | null;
  neutral: boolean;
  tv: string[];
  strip: StripGame[];
  /** The page's own sections under Overview (Your bets). */
  children?: React.ReactNode;
}) {
  const [row, setRow] = useState(initial);
  const [summary, setSummary] = useState(initialSummary);
  const [polls, setPolls] = useState(0);
  const [tab, setTab] = useState<Tab>("overview");
  const rowRef = useRef(row);
  useEffect(() => {
    rowRef.current = row;
  }, [row]);

  const shown = useMemo(() => merged(row, summary), [row, summary]);
  const live = isLive(shown);
  // Poll while the game is on, or due within the hour (it may have started).
  // eslint-disable-next-line react-hooks/purity
  const hot = useMemo(() => live || (shown.status === "scheduled" && Date.parse(shown.at) - Date.now() < 3_600_000 && Date.parse(shown.at) - Date.now() > -6 * 3_600_000), [live, shown]);

  const poll = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    if (initialSummary) {
      const res = await pollSummary(rowRef.current.id);
      if (res.ok && res.summary) setSummary(res.summary);
    } else {
      const res = await pollLive([rowRef.current.id]);
      if (res.ok) setRow((cur) => applyPatches([cur], res.rows)[0]);
    }
    setPolls((n) => n + 1);
  }, [initialSummary]);

  useEffect(() => {
    if (!hot) return;
    const id = window.setInterval(poll, (initialSummary ? SUMMARY_SECONDS : POLL_SECONDS) * 1000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [hot, poll, initialSummary]);

  const scored = !!shown.score && (live || shown.status === "final" || shown.status === "suspended");
  const [sa, sh] = [num(shown.score?.away), num(shown.score?.home)];
  const decided = shown.status === "final" && sa !== null && sh !== null && sa !== sh;
  const s = summary;
  const tabs: [Tab, string][] = [
    ["overview", "Overview"],
    ...(s && (s.players.away.length || s.players.home.length) ? [["players", "Player stats"] as [Tab, string]] : []),
    ...(s && s.teamStats.length ? [["team", "Team stats"] as [Tab, string]] : []),
    ...(s && s.plays.length ? [["plays", "Play-by-play"] as [Tab, string]] : []),
  ];
  const lastTeam = s?.lastPlay?.side ? teams[s.lastPlay.side] : undefined;

  return (
    <div data-testid="game-view">
      {strip.length > 1 && (
        <nav aria-label="Other games" data-testid="game-strip" className="scroll-row -mx-4 mb-6 flex gap-2 overflow-x-auto px-4 lg:mx-0 lg:px-0">
          {strip.map((g) => {
            const on = g.id === shown.id;
            const r = g.id === shown.id ? shown : g.row;
            const gl = isLive(r);
            const sc = !!r.score && (gl || r.status === "final");
            const c = gl ? liveClock(r) : null;
            return (
              <Link key={g.id} href={`/gameday/${g.id}`} aria-current={on ? "page" : undefined}
                    className={`flex h-12 shrink-0 items-center gap-2 rounded-tile border px-2.5 ${
                      on ? "border-text bg-band" : "border-border hover:border-text-muted"}`}>
                {g.away.logo ? <TeamLogo team={g.away} size="sm" /> : <span className="text-sm">{g.row.away}</span>}
                {sc && <span className="text-base tabular-nums text-text">{r.score?.away}</span>}
                <span className={`whitespace-nowrap text-xs tabular-nums ${gl ? "text-hit" : "text-text-muted"}`}>
                  {c ? `${c.period}${c.clock ? ` - ${c.clock}` : ""}` : statusLabel(r) ?? startLabel(r)}
                </span>
                {sc && <span className="text-base tabular-nums text-text">{r.score?.home}</span>}
                {g.home.logo ? <TeamLogo team={g.home} size="sm" /> : <span className="text-sm">{g.row.home}</span>}
              </Link>
            );
          })}
        </nav>
      )}

      <div className="flex items-start justify-between gap-3">
        <div>
          <Link href="/gameday" className="tap inline-flex items-center text-lg text-text hover:text-accent-ink">
            {leagueLabel} <span aria-hidden className="ml-1 text-text-muted">›</span>
          </Link>
          {s?.note && <p className="text-sm text-text-muted">{s.note}</p>}
        </div>
        <span className="flex items-center gap-2 pt-2">
          {live && <LiveMark />}
          {shown.test && <span className="label-mono rounded-pill border border-border px-1.5 text-text-muted">test</span>}
        </span>
      </div>

      <div data-testid="event-score" data-status={shown.status} data-polling={hot ? "true" : "false"} data-polls={polls}
           className="mt-4">
        <div className="grid grid-matchup items-center gap-3">
          <div className="flex items-center justify-between gap-2">
            {teams.away.logo ? <TeamLogo team={teams.away} size="md" /> : <span className="block size-14" />}
            {scored && <BigScore value={shown.score?.away} testId="event-score-away" muted={decided && sa! < sh!} />}
          </div>
          <div className="min-w-22"><Middle row={shown} today={today} /></div>
          <div className="flex items-center justify-between gap-2">
            {scored && <BigScore value={shown.score?.home} testId="event-score-home" muted={decided && sh! < sa!} />}
            <span className="ml-auto">{teams.home.logo ? <TeamLogo team={teams.home} size="md" /> : <span className="block size-14" />}</span>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-4">
          <TeamName name={names.away} rank={ranks.away ?? s?.away.rank} record={s?.away.record} side="away"
                    possession={live && s?.situation?.offense === "away"} />
          <TeamName name={names.home} rank={ranks.home ?? s?.home.rank} record={s?.home.record} side="home"
                    possession={live && s?.situation?.offense === "home"} />
        </div>
        {!scored && <span data-testid="event-score-away" hidden />}
        {!scored && <span data-testid="event-score-home" hidden />}

        {live && s?.situation && <Field s={s.situation} away={teams.away} home={teams.home} abbrs={[s.away.abbr, s.home.abbr]} />}
        {live && s?.lastPlay && (
          <>
            <LastPlay play={s.lastPlay} team={lastTeam} />
            {s.situation?.drive && <p className="mt-1.5 px-3 text-sm text-text-muted">Current drive: {s.situation.drive}</p>}
          </>
        )}
      </div>

      <div role="tablist" aria-label="Game" className="scroll-row -mx-4 mt-6 flex gap-2 overflow-x-auto px-4 lg:mx-0 lg:px-0">
        {tabs.map(([t, label]) => <Pill key={t} on={tab === t} onClick={() => setTab(t)} testId={`game-tab-${t}`}>{label}</Pill>)}
      </div>

      <div className="mt-5">
        {tab === "overview" && (
          <>
            <div className="flex gap-3">
              <div data-testid="detail-watch"
                   className="flex min-h-11 min-w-0 flex-1 items-center justify-center gap-2 rounded-pill border border-border px-3 text-base text-text">
                <Television aria-hidden size={18} className="shrink-0 text-text-muted" />
                <Networks tv={tv} testId="detail-tv" />
              </div>
              <div data-testid="detail-kalshi" className="flex flex-1"><KalshiButton k={kalshi} /></div>
            </div>
            {kalshi.reason && kalshi.state !== "linked" && (
              <p className="mt-2 text-center text-sm text-text-muted" aria-hidden>{kalshi.reason}</p>
            )}

            {s && (shown.status !== "scheduled" || s.away.lines.length > 0) && (
              <div className="mt-6"><LineScore s={s} /></div>
            )}

            {s && <Leaders s={s} teams={teams} />}

            {s && s.scoring.length > 0 && (
              <section className="mt-8">
                <Heading>Scoring summary</Heading>
                <PlayList plays={s.scoring} s={s} teams={teams} testId="scoring-summary" />
              </section>
            )}

            <section data-testid="detail-venue" className="mt-8 border-t border-border pt-4 text-base">
              <p className="text-text">Venue: <span className="text-text-muted">{s?.venue ?? venue ?? "Not listed"}</span></p>
              {neutral && <p data-testid="detail-neutral" className="mt-1 text-sm text-text-muted">Neutral site</p>}
              {s?.attendance && <p className="mt-1 text-text">Attendance: <span className="tabular-nums text-text-muted">{s.attendance.toLocaleString("en-US")}</span></p>}
              {s?.weather && <p className="mt-1 text-text">Weather: <span className="text-text-muted">{s.weather}</span></p>}
              <p className="mt-1 text-text">Timezone: <span className="text-text-muted">All times are in Central Time</span></p>
            </section>

            {children}
          </>
        )}
        {tab === "players" && s && <PlayerStats s={s} teams={teams} />}
        {tab === "team" && s && <TeamStats s={s} teams={teams} />}
        {tab === "plays" && s && <PlayList plays={s.plays} s={s} teams={teams} testId="play-by-play" />}
      </div>
    </div>
  );
}
