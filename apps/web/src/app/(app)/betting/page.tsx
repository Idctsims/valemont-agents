import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { BoardView } from "@/components/betting/board-view";
import { ChipFace } from "@/components/ui/chip";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { KIND_LABEL, boardHref, creditsLine, parseFilters, pickSport, type Kind } from "@/lib/betting/board";
import { loadCredits, loadGames, loadSports } from "@/lib/betting/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting" };

// Betting (Chat 8 Phases 1-2; docs/kalshi_board.md, docs/consensus.md). The Board tab: every open
// Kalshi sports market for the next ~36 h (db/071), one sport at a time, its
// games collapsed; a game's markets load on expand, each binary market with
// its consensus row (db/073). Filters live in the URL:
// ?sport=, ?kind=lines|props|other, ?combo=1. Read-only: nothing here bets,
// and nothing ever bets automatically (CLAUDE.md §1).

type Params = { sport?: string | string[]; kind?: string | string[]; combo?: string | string[] };

const KIND_CHIPS: readonly (Kind | null)[] = [null, "game_line", "prop", "other"];

export default async function BettingPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const filters = parseFilters(await searchParams);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [sports, credits] = await Promise.all([loadSports(supabase, withTests), loadCredits(supabase)]);
  const sport = pickSport(sports, filters.sport);
  const games = sport ? await loadGames(supabase, { sport, kind: filters.kind, combo: filters.combo, withTests }) : [];
  const shown = { ...filters, sport };

  return (
    <>
      <PageHeader label="Sports" title="Betting" />

      <nav aria-label="Betting views" className="mb-6 flex gap-6 border-b border-border" role="tablist">
        <span role="tab" aria-selected="true" data-testid="betting-tab-board"
              className="-mb-px border-b-2 border-accent pb-2 text-base text-text">
          Board
        </span>
      </nav>

      {/* The Odds API's month (db/073): the consensus engine's only paid source. */}
      <p data-testid="betting-credits" className="label-mono -mt-3 mb-5 text-text-muted">{creditsLine(credits)}</p>

      {sports.length === 0 ? (
        <p data-testid="board-empty" className="border-t border-border pt-4 text-base text-text-muted lg:max-w-3xl">
          Nothing on the board yet. It fills at 9:30 AM and 3:30 PM with every Kalshi sports market for the next 36 hours.
        </p>
      ) : (
        <>
          <nav aria-label="Sport" data-testid="board-sports" className="-ml-1 mb-2 flex flex-wrap items-center">
            {sports.map((c) => (
              <Link
                key={c.sport}
                href={boardHref({ ...filters, sport: c.sport })}
                data-testid="board-sport-chip"
                data-sport={c.sport}
                aria-current={c.sport === sport ? "page" : undefined}
                className="tap group inline-flex items-center justify-center px-1"
              >
                <ChipFace selected={c.sport === sport}>
                  {c.sport} <span className="ml-1.5 font-mono text-text-muted">{c.games}</span>
                </ChipFace>
              </Link>
            ))}
          </nav>

          <nav aria-label="Filters" data-testid="board-filters" className="-ml-1 mb-6 flex flex-wrap items-center">
            {KIND_CHIPS.map((k) => (
              <Link
                key={k ?? "all"}
                href={boardHref({ ...shown, kind: k })}
                data-testid={`board-kind-${k ?? "all"}`}
                aria-current={filters.kind === k ? "page" : undefined}
                className="tap group inline-flex items-center justify-center px-1"
              >
                <ChipFace selected={filters.kind === k}>{k ? KIND_LABEL[k] : "All"}</ChipFace>
              </Link>
            ))}
            <Link
              href={boardHref({ ...shown, combo: !filters.combo })}
              data-testid="board-combo-filter"
              aria-current={filters.combo ? "page" : undefined}
              className="tap group inline-flex items-center justify-center px-1"
            >
              <ChipFace selected={filters.combo}>Combo-eligible only</ChipFace>
            </Link>
          </nav>

          {/* Keyed by the view, so a filter switch starts collapsed. */}
          <BoardView key={boardHref(shown)} games={games} kind={filters.kind} combo={filters.combo} />
        </>
      )}
    </>
  );
}
