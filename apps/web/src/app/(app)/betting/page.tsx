import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { BettingFrame } from "@/components/betting/frame";
import { GameCard } from "@/components/betting/game-card";
import { requireOwner } from "@/lib/auth";
import { asOfLabel, boardHeadline, boardHref, byDay, claimedFilter, creditsLine, dayLabel, pickSport } from "@/lib/betting/board";
import { loadCards, loadCredits, loadSports } from "@/lib/betting/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting" };

// The Board (Chat 8 Phase 3.5, Workstream B), as the approved mock draws it
// (owner, Nov 15: "match the mock on all of them", its palette, logos and
// team colours): the date, a compact sport switcher, the headline, the sport's
// board with All games / Claimed edges, and its games as cards under green day
// bands (db/083 kalshi_board_cards). A game opens as its own page. Every open
// Kalshi sports market for the next ~36 h (db/071). Read-only: nothing bets.
// Not drawn: the mock's track record band and example tickets (owner, Nov 15:
// none until Workstream A signs a figure off), National TV (dropped).

type Params = { sport?: string | string[]; claimed?: string | string[] };

export default async function BettingPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const wanted = Array.isArray(params.sport) ? params.sport[0] : params.sport;
  const want = wanted?.trim().slice(0, 40) || null;
  const claimed = (Array.isArray(params.claimed) ? params.claimed[0] : params.claimed) === "1";
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [sports, credits] = await Promise.all([loadSports(supabase, withTests), loadCredits(supabase)]);
  const sport = pickSport(sports, want);
  const all = sport ? await loadCards(supabase, sport, withTests) : [];
  const nClaimed = claimedFilter(all, true).length;
  const games = claimedFilter(all, claimed).map((c) => ({ card: c, g: c.g, at: new Date(c.at * 1000).toISOString() }));
  const now = new Date();
  const head = boardHeadline(all.map((c) => ({ at: new Date(c.at * 1000).toISOString() })), now);
  const asof = all.find((c) => c.asof)?.asof;

  return (
    <BettingFrame>
      <header className="flex flex-col gap-5 pb-7">
        <p className="label-mono text-text-muted">Kalshi board · {dayLabel(now.toISOString())}</p>
        {sports.length > 0 && (
          <nav aria-label="Sport" data-testid="board-sports"
               className="flex max-w-full gap-0.5 self-start overflow-x-auto rounded-tile border border-border p-0.75">
            {sports.map((c) => (
              <Link key={c.sport} href={boardHref({ sport: c.sport, kind: null, combo: false, claimed })}
                    data-testid="board-sport-chip" data-sport={c.sport}
                    aria-current={c.sport === sport ? "page" : undefined}
                    className={`inline-flex min-h-8.5 shrink-0 items-center whitespace-nowrap rounded-tile px-3.25 font-mono text-label uppercase transition-colors ${
                      c.sport === sport ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}>
                {c.sport}<span className="ml-1.25 opacity-70">{c.games}</span>
              </Link>
            ))}
          </nav>
        )}
        <h1 className="font-display text-display text-text text-balance">
          {head.lead && `${head.lead} `}<em className="text-accent-ink">{head.n}</em> {head.tail}
        </h1>
        <p className="max-w-measure text-lg text-text-muted text-pretty">
          Every Kalshi price on the slate next to what the sportsbooks say it should be. Tap a price to add it to your
          slip. Open a game for its players, other lines and quarters.
        </p>
        <div className="flex flex-col gap-0.75">
          {asof && <p data-testid="board-asof" className="label-mono text-text-muted">Prices {asOfLabel(asof)}</p>}
          <p className="label-mono text-text-muted">Paper only · nothing bets</p>
          {/* The Odds API's month (db/073): the consensus engine's only paid source. */}
          <p data-testid="betting-credits" className="label-mono text-text-muted">{creditsLine(credits)}</p>
        </div>
      </header>

      {sports.length === 0 ? (
        <p data-testid="board-empty" className="border-t border-border pt-4 text-base text-text-muted">
          Nothing on the board yet. It fills at 9:30 AM and 3:30 PM with every Kalshi sports market for the next 36 hours.
        </p>
      ) : (
        <section className="-mx-4 border-t border-border bg-band px-4 pb-14 pt-9 lg:mx-0 lg:rounded-inner lg:border lg:px-6">
          <div className="mb-4.5 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
            <h2 className="font-display text-3xl text-text">{sport} board</h2>
            <p className="max-w-measure text-sm text-text-muted">
              The big number is Kalshi&apos;s price to win: what $1 pays back (×) or the chance it implies (%), your
              choice at the top. The bar is the books&apos; consensus. Spreads and totals show the books&apos; line.
            </p>
          </div>

          <nav aria-label="Filter" data-testid="board-filter" className="mb-5 flex gap-6 overflow-x-auto border-b border-border">
            {[{ on: false, face: "All games", n: all.length }, { on: true, face: "Claimed edges", n: nClaimed }].map((f) => (
              <Link key={f.face} href={boardHref({ sport, kind: null, combo: false, claimed: f.on })}
                    data-testid={f.on ? "filter-claimed" : "filter-all"} aria-current={claimed === f.on ? "page" : undefined}
                    className={`-mb-px inline-flex min-h-10 items-center whitespace-nowrap border-b-2 px-0.5 pb-3 text-sm transition-colors ${
                      claimed === f.on ? "border-accent-ink font-semibold text-text" : "border-transparent font-medium text-text-muted hover:text-text"}`}>
                {f.face}<span className="ml-1.25 font-mono text-xs font-normal">{f.n}</span>
              </Link>
            ))}
          </nav>

          {games.length === 0 ? (
            <p data-testid="board-no-games" className="pt-2 text-base text-text-muted">
              {claimed && all.length > 0
                ? "No claimed edges in this sport right now. An edge is claimed only on books prices, fresh both sides, positive under every de-vig method."
                : "No games for this sport on the board."}
            </p>
          ) : (
            <div data-testid="board-games" className="flex flex-col gap-6">
              {byDay(games).map((d) => (
                <section key={d.day} aria-label={d.day}>
                  <p data-testid="board-day"
                     className="flex items-center justify-between rounded-t-tile bg-accent px-4 py-2.75 text-sm font-semibold text-on-accent">
                    <span>{d.day}</span>
                    <span className="label-mono opacity-85">{d.games.length} {d.games.length === 1 ? "game" : "games"}</span>
                  </p>
                  <div className="grid gap-3 pt-3 sm:grid-cols-2">
                    {d.games.map((g) => <GameCard key={g.g} card={g.card} sport={sport ?? ""} />)}
                  </div>
                </section>
              ))}
            </div>
          )}
        </section>
      )}
    </BettingFrame>
  );
}
