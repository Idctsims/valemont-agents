import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";
import { SquaresFour, Star } from "@phosphor-icons/react/ssr";

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
  { label: "My five", all: false },
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

      {/* Whose games: a segmented switch, the shown side raised on the track (owner, Nov 20: a better
          way than two chips). Each side is a link, so the choice stays in the URL. */}
      <nav aria-label="Sports shown" data-testid="gameday-chips"
           className="-mt-2 mb-6 grid w-full grid-cols-2 gap-1 rounded-pill border border-border bg-surface-2 p-1 sm:w-fit">
        {CHIPS.map((c) => {
          const on = c.all === all;
          const Icon = c.all ? SquaresFour : Star;
          return (
            <Link
              key={c.label}
              href={c.all ? "/gameday?sports=all" : "/gameday"}
              data-testid={`gameday-chip-${c.all ? "all" : "five"}`}
              aria-current={on ? "page" : undefined}
              className={`inline-flex min-h-10 items-center justify-center gap-2 whitespace-nowrap rounded-pill border px-5 text-sm font-semibold transition-colors ${
                on ? "border-accent bg-accent text-on-accent" : "border-transparent text-text-muted hover:text-text"}`}
            >
              <Icon size={16} weight={on ? "fill" : "regular"} aria-hidden />
              {c.label}
            </Link>
          );
        })}
      </nav>

      {/* Keyed by the view, so a chip switch starts from the server's rows. */}
      <section className="-mx-4 border-t border-border bg-band px-4 pb-14 lg:mx-0 lg:rounded-inner lg:border lg:px-6">
        <SlateView key={`${today}-${all}`} initial={rows} today={today} all={all} />
      </section>
    </>
  );
}
