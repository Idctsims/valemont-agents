import type { Metadata } from "next";
import { headers } from "next/headers";
import Link from "next/link";

import { BettingFrame } from "@/components/betting/frame";
import { PayoutBuilder } from "@/components/betting/payout-builder";
import { PropsView } from "@/components/betting/props-view";
import { requireOwner } from "@/lib/auth";
import { pickSport } from "@/lib/betting/board";
import { loadSingles, loadSports } from "@/lib/betting/data";
import { parseSingles, singlesHref } from "@/lib/betting/singles";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting · Props" };

// The Props tab (owner, Nov 25; docs/props_tab.md): every single on the board
// for one sport, YES and NO sides, games not yet started, in three price tiers
// (Safer 60-85c, Mid 30-60c, Lotto 5-30c), read through db/093's
// kalshi_singles. Opens on Props; Lines is one tap. A side goes into the slip,
// which prices sides from any games and sports as one combo. Paper: nothing bets.

export default async function PropsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requireOwner();
  const supabase = await createClient();
  const q = parseSingles(await searchParams);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const sports = await loadSports(supabase, withTests);
  const sport = pickSport(sports, q.sport ?? null);
  let page = null;
  let failed = false;
  if (sport) {
    try {
      page = await loadSingles(supabase, sport, q, withTests);
    } catch (e) {
      console.error(`props: ${(e as Error).message}`);
      failed = true;
    }
  }

  return (
    <BettingFrame>
      <header className="flex flex-col gap-3 pb-5 lg:gap-4 lg:pb-6" data-testid="props-page" data-sport={sport ?? ""}>
        <p className="label-mono text-text-muted">Paper · nothing here is bet</p>
        {/* Phones: the tab above already says Props; the title is for screen readers there (owner, Nov 25). */}
        <h1 className="sr-only font-display text-text lg:not-sr-only lg:text-display">Props</h1>
        <p className="hidden max-w-measure text-base text-text-muted text-pretty lg:block">
          Single bets in three price tiers. Add any to your slip, and it prices them together as one parlay.
        </p>
        {/* Folded on purpose (phone screenshots, Nov 25): open, the words pushed every row below the first screen
            (phones fold the intro in here too). "Build to a payout" (E1) shares the row, so the rows stay put. */}
        <div className="flex flex-wrap items-start gap-x-6">
          <PayoutBuilder />
          <details data-testid="props-how" className="max-w-measure text-sm text-text-muted">
            <summary className="tap flex items-center font-semibold text-accent-ink">How the prices work</summary>
            <p className="text-pretty lg:hidden">
              Single bets in three price tiers. Add any to your slip, and it prices them together as one parlay.
            </p>
            <p className="mt-2 text-pretty lg:mt-0">
              The price is after Kalshi&apos;s fee. Where the sportsbooks or ESPN price the same bet, the side shows its
              edge against them. Everywhere else the only price is Kalshi&apos;s own, so the number is what the side costs
              to trade, not an edge.
            </p>
          </details>
        </div>
        {sports.length > 0 && (
          <nav aria-label="Sport" data-testid="props-sports"
               className="flex max-w-full gap-0.5 self-start overflow-x-auto rounded-tile border border-border p-0.75">
            {sports.map((c) => (
              <Link key={c.sport} href={singlesHref(q, { sport: c.sport, q: undefined })}
                    data-testid="props-sport-chip" data-sport={c.sport} aria-current={c.sport === sport ? "page" : undefined}
                    className={`inline-flex min-h-8.5 shrink-0 items-center whitespace-nowrap rounded-tile px-3.25 font-mono text-label uppercase transition-colors ${
                      c.sport === sport ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}>
                {c.sport}
              </Link>
            ))}
          </nav>
        )}
      </header>

      {!sport ? (
        <p data-testid="props-empty" className="border-t border-border pt-4 text-base text-text-muted">
          Nothing on the board yet. It fills at 9:30 AM and 3:30 PM with every Kalshi sports market for the next 36 hours.
        </p>
      ) : failed || !page ? (
        <p role="alert" data-testid="props-error" className="max-w-measure border-t border-border pt-4 text-lg text-text">
          The props didn&apos;t load. Try again in a moment.
        </p>
      ) : (
        <PropsView key={singlesHref({ ...q, sport })} sport={sport} q={{ ...q, sport }} page={page} />
      )}
    </BettingFrame>
  );
}
