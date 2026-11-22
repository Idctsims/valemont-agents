import Link from "next/link";

import type { Shelf, ShelfTitle } from "@/lib/screening/types";
import { SHELVES } from "@/lib/screening/types";

import { Poster } from "./search-box";

// The shelves as Letterboxd's Watchlist (owner, 2026-11-22; docs/screening.md
// §9): Watching, Want and Finished as three tabs (links, so the choice is in
// the URL), each a grid of posters, 3 across on the phone and 5 from lg; a
// tap opens the title page, where its buttons move it.

export function ShelfTabs({ current, counts }: { current: Shelf; counts: Record<Shelf, number> }) {
  return (
    <nav aria-label="Shelves" data-testid="shelf-tabs" className="grid grid-cols-3 gap-1 rounded-inner bg-surface p-1">
      {SHELVES.map((s) => {
        const on = s.shelf === current;
        return (
          <Link key={s.shelf} href={s.shelf === "watching" ? "/screening#shelves" : `/screening?shelf=${s.shelf}#shelves`}
                scroll={false} data-testid={`shelf-tab-${s.shelf}`} aria-current={on ? "page" : undefined}
                className={`tap inline-flex items-center justify-center gap-1.5 rounded-tile text-base font-semibold transition-colors ${
                  on ? "bg-surface-2 text-text" : "text-text-muted hover:text-text"}`}>
            {s.shelf === "want" ? "Watchlist" : s.label}
            <span className="font-mono text-xs font-normal tabular-nums text-text-muted">{counts[s.shelf]}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function PosterGrid({ rows, empty, testId }: { rows: ShelfTitle[]; empty: string; testId: string }) {
  if (rows.length === 0) return <p data-testid={`${testId}-empty`} className="mt-4 text-sm text-text-muted">{empty}</p>;
  return (
    <ul data-testid={testId} className="mt-4 grid grid-cols-3 gap-2.5 lg:grid-cols-5 lg:gap-4">
      {rows.map((r) => (
        <li key={r.titleId} data-testid="shelf-title" data-title-id={r.titleId}>
          <Link href={`/screening/title/${r.kind}/${r.tmdbId}`} aria-label={r.name} className="group relative block">
            <span className="block overflow-hidden rounded-inner ring-1 ring-border transition group-hover:ring-accent-ink">
              <Poster path={r.posterPath} name={r.name} fill />
            </span>
            {r.isTest && (
              <span className="label-mono absolute left-1.5 top-1.5 rounded-pill bg-bg/80 px-1.5 text-text">test</span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}
