import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { SlateView } from "@/components/gameday/slate-view";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { loadSlate } from "@/lib/gameday/data";
import { gameDay } from "@/lib/gameday/slate";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Game Day" };

// Game Day (Chat 7 Phase 3; docs/gameday.md): Live now · Tonight · This week
// from sports_events (ESPN), the owner's five by default; All sports adds the
// other leagues for tonight and live, the same chip pattern as /sports, the
// choice kept in the URL (?sports=all). "Tonight" is the game day, 05:00 to
// 05:00 Chicago. Bets attach in Chat 9; no Kalshi price here (Chat 8).

type Params = { sports?: string | string[] };

const CHIPS = [
  { label: "The five", all: false },
  { label: "All sports", all: true },
] as const;

export default async function GameDayPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const all = (Array.isArray(params.sports) ? params.sports[0] : params.sports) === "all";
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  // The game day is read per request (it turns at 05:00 Chicago).
  // eslint-disable-next-line react-hooks/purity
  const today = gameDay(new Date(Date.now()));
  const rows = await loadSlate(supabase, { day: today, all, withTests });

  return (
    <>
      <PageHeader label="Sports" title="Game Day" />

      {/* The betting board's switcher: a compact segmented pill, the accent on the one shown. */}
      <nav aria-label="Sports shown" data-testid="gameday-chips"
           className="mb-7 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-tile border border-border p-0.75">
        {CHIPS.map((c) => (
          <Link
            key={c.label}
            href={c.all ? "/gameday?sports=all" : "/gameday"}
            data-testid={`gameday-chip-${c.all ? "all" : "five"}`}
            aria-current={c.all === all ? "page" : undefined}
            className={`inline-flex min-h-8.5 shrink-0 items-center whitespace-nowrap rounded-tile px-3.25 font-mono text-label uppercase transition-colors ${
              c.all === all ? "bg-accent text-on-accent" : "text-text-muted hover:text-text"}`}
          >
            {c.label}
          </Link>
        ))}
      </nav>

      {/* Keyed by the view, so a chip switch starts from the server's rows. */}
      <section className="-mx-4 border-t border-border bg-band px-4 pb-14 pt-9 lg:mx-0 lg:rounded-inner lg:border lg:px-6">
        <SlateView key={`${today}-${all}`} initial={rows} today={today} all={all} />
      </section>
    </>
  );
}
