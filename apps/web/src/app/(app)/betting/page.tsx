import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { BettingFrame } from "@/components/betting/frame";
import { ChipFace } from "@/components/ui/chip";
import { requireOwner } from "@/lib/auth";
import {
  asOfLabel, boardHeadline, boardHref, byDay, countsLine, creditsLine, dayLabel, pickSport, startLabel,
  type BoardGame,
} from "@/lib/betting/board";
import { loadCredits, loadGames, loadSports } from "@/lib/betting/data";
import { sidesOf } from "@/lib/betting/game";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting" };

// The Board (Chat 8 Phase 3.5, Workstream B; the approved mock): one sport at
// a time, its games as cards by day; a game opens as its own page
// (/betting/game/<ticker>) with its tabs. Every open Kalshi sports market for
// the next ~36 h (db/071). ?sport= in the URL. Read-only: nothing bets.
//
// Not as drawn: the mock's cards carry each side's moneyline price and the
// consensus bar; kalshi_board_games returns counts only, so those live on the
// game page's header (a card reader would be a new migration).

type Params = { sport?: string | string[] };

function GameCard({ game }: { game: BoardGame }) {
  const sides = sidesOf(game.t);
  const total = (game.l ?? 0) + (game.p ?? 0) + (game.o ?? 0) + (game.s ?? 0);
  const href = `/betting/game/${encodeURIComponent(game.g)}`;
  return (
    <article data-testid="board-game" data-game={game.g} data-league={game.lg}
             className="flex min-w-0 flex-col overflow-hidden rounded-inner border border-border bg-surface">
      <p className="flex justify-between gap-3 border-b border-border px-4 py-2 text-xs text-text-muted">
        <span data-testid="game-start" className="text-text">{startLabel(game.at)}</span>
        <span className="flex items-center gap-2">
          {game.test && <span className="label-mono rounded-pill border border-border px-1.5">test</span>}
          <span className="label-mono">{(game.lg ?? "").toUpperCase()}</span>
        </span>
      </p>
      <Link href={href} className="block px-4 pb-3 pt-4 hover:bg-surface-2">
        {sides ? (
          <span className="grid grid-matchup items-start gap-2 text-center">
            <span className="flex min-w-0 flex-col">
              <span className="font-display text-3xl text-text">{sides.a.code}</span>
              <span className="truncate text-xs text-text-muted">{sides.a.name}</span>
            </span>
            <span aria-hidden className="pt-3 text-xs text-text-muted">at</span>
            <span className="flex min-w-0 flex-col">
              <span className="font-display text-3xl text-text">{sides.h.code}</span>
              <span className="truncate text-xs text-text-muted">{sides.h.name}</span>
            </span>
          </span>
        ) : (
          <span className="block font-display text-2xl text-text">{game.t}</span>
        )}
        <span className="sr-only">{game.t}</span>
      </Link>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-xs text-text-muted">
        <span data-testid="game-counts">{countsLine(game)}</span>
        {game.c ? <span className="label-mono text-accent">{game.c} combo</span> : null}
        <span className="label-mono">{asOfLabel(game.asof)}</span>
      </p>
      <Link href={href} data-testid="board-game-open"
            className="mt-auto flex min-h-13 items-center justify-between gap-3 border-t border-border bg-surface-2 px-4 py-2 hover:bg-surface-3">
        <span>
          <span className="block text-xs text-text-muted"><span className="text-text">{total}</span> markets</span>
          <span className="block text-sm text-accent">Lines, players, halves and quarters</span>
        </span>
        <span aria-hidden className="text-accent">→</span>
      </Link>
    </article>
  );
}

export default async function BettingPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const wanted = (await searchParams).sport;
  const want = (Array.isArray(wanted) ? wanted[0] : wanted)?.trim().slice(0, 40) || null;
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [sports, credits] = await Promise.all([loadSports(supabase, withTests), loadCredits(supabase)]);
  const sport = pickSport(sports, want);
  const games = sport ? await loadGames(supabase, { sport, kind: null, combo: false, withTests }) : [];
  const now = new Date();
  const head = boardHeadline(games, now);

  return (
    <BettingFrame>
      <header className="mb-6">
        <p className="label-mono text-text-muted">Kalshi board · {dayLabel(now.toISOString())}</p>
        <h1 className="mt-3 font-display text-4xl text-text text-balance lg:text-display">
          {head.lead && `${head.lead} `}<em className="text-accent">{head.n}</em> {head.tail}
        </h1>
        <p className="mt-4 max-w-measure text-base text-text-muted text-pretty">
          Every Kalshi price on the slate, beside what the sportsbooks say where they say it. Open a game for its
          lines, players, halves and quarters; tap a price to add it to your slip.
        </p>
        <p className="label-mono mt-3 text-text-muted">Paper only · nothing bets</p>
        {/* The Odds API's month (db/073): the consensus engine's only paid source. */}
        <p data-testid="betting-credits" className="label-mono mt-1 text-text-muted">{creditsLine(credits)}</p>
      </header>

      {sports.length === 0 ? (
        <p data-testid="board-empty" className="border-t border-border pt-4 text-base text-text-muted">
          Nothing on the board yet. It fills at 9:30 AM and 3:30 PM with every Kalshi sports market for the next 36 hours.
        </p>
      ) : (
        <>
          <nav aria-label="Sport" data-testid="board-sports" className="-ml-1 mb-4 flex flex-wrap items-center">
            {sports.map((c) => (
              <Link key={c.sport} href={boardHref({ sport: c.sport, kind: null, combo: false })}
                    data-testid="board-sport-chip" data-sport={c.sport}
                    aria-current={c.sport === sport ? "page" : undefined}
                    className="tap group inline-flex items-center justify-center px-1">
                <ChipFace selected={c.sport === sport}>
                  {c.sport} <span className="ml-1.5 font-mono text-text-muted">{c.games}</span>
                </ChipFace>
              </Link>
            ))}
          </nav>

          {games.length === 0 ? (
            <p data-testid="board-no-games" className="border-t border-border pt-4 text-base text-text-muted">
              No games for this sport on the board.
            </p>
          ) : (
            <div data-testid="board-games" className="flex flex-col gap-6">
              {byDay(games).map((d) => (
                <section key={d.day} aria-label={d.day}>
                  <p data-testid="board-day"
                     className="flex items-center justify-between rounded-t-inner bg-accent px-4 py-2.5 text-sm text-on-accent">
                    <span>{d.day}</span>
                    <span className="label-mono">{d.games.length} {d.games.length === 1 ? "game" : "games"}</span>
                  </p>
                  <div className="grid gap-3 pt-3 sm:grid-cols-2">
                    {d.games.map((g) => <GameCard key={g.g} game={g} />)}
                  </div>
                </section>
              ))}
            </div>
          )}
        </>
      )}
    </BettingFrame>
  );
}
