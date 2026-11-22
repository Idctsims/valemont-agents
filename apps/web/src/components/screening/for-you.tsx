import Link from "next/link";

import type { Rec } from "@/lib/screening/recs";

import { Poster } from "./search-box";

// The For you row (Chat 4 Phase 4; db/053), as Letterboxd's "Popular this
// week" (owner, 2026-11-22; docs/screening.md §9): posters only, Film or
// Series under each, a tap opens the title page. Liking, the Watchlist and
// Not interested live on the title page now; the database still drops a
// title from For you the moment it is shelved or reacted to.

export function ForYouRow({ recs }: { recs: Rec[] }) {
  if (recs.length === 0) {
    return (
      <p data-testid="for-you-empty" className="mt-2 text-sm text-text-muted">
        Nothing new to suggest right now. The next run is tonight at 04:30.
      </p>
    );
  }
  return (
    <ul data-testid="for-you-list"
        className="scroll-row -mx-4 mt-3 flex snap-x scroll-px-4 gap-3 overflow-x-auto px-4 pb-2 lg:mx-0 lg:grid lg:grid-cols-6 lg:overflow-visible lg:px-0">
      {recs.map((r) => (
        <li key={r.titleId} data-testid="for-you-card" data-title-id={r.titleId} className="w-28 shrink-0 snap-start lg:w-auto">
          <Link href={`/screening/title/${r.kind}/${r.tmdbId}`} aria-label={r.name} className="group block">
            <span className="block overflow-hidden rounded-inner ring-1 ring-border transition group-hover:ring-accent-ink">
              <Poster path={r.posterPath} name={r.name} fill />
            </span>
          </Link>
          <p className="label-mono mt-1.5 text-text-muted">
            {r.kind === "tv" ? "Series" : "Film"}
            {r.isTest && <span className="ml-2 rounded-pill border border-text-muted px-1.5 normal-case tracking-normal text-text">test</span>}
          </p>
        </li>
      ))}
    </ul>
  );
}
