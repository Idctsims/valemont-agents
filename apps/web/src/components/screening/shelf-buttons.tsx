"use client";

import { useOptimistic, useState, useTransition } from "react";

import { moveTitle, putOnShelf } from "@/app/(app)/screening/actions";
import type { ActionResult } from "@/lib/action-result";
import { SHELVES, type Shelf } from "@/lib/screening/types";

// One tap moves a title between the shelves (db/051): Watching, Want,
// Finished. The current shelf is pressed. A title already in the database
// moves by its id; a search result goes on by its TMDB id (added to `titles`
// first). Optimistic; the server render that follows replaces it. While a
// move is in flight the group is aria-busy; a refusal shows its reason.

type Target = { titleId: number } | { kind: "movie" | "tv"; tmdbId: number };

export function ShelfButtons({ target, current, compact = false }: { target: Target; current: Shelf | null; compact?: boolean }) {
  const [shelf, setShelf] = useOptimistic(current);
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function tap(next: Shelf) {
    if (next === shelf) return;
    start(async () => {
      setShelf(next);
      try {
        const r: ActionResult = "titleId" in target
          ? await moveTitle({ titleId: target.titleId, shelf: next })
          : await putOnShelf({ kind: target.kind, tmdbId: target.tmdbId, shelf: next });
        setError(r.ok ? null : r.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  const shown = compact ? SHELVES.filter((s) => s.shelf !== "finished") : SHELVES;
  return (
    <div className="shrink-0">
      <div role="group" aria-label="Shelf" aria-busy={pending} data-testid="shelf-buttons" data-shelf={shelf ?? "none"}
           className="flex flex-wrap items-center gap-1">
        {shown.map((s) => (
          <button
            key={s.shelf}
            type="button"
            data-testid={`shelf-${s.shelf}`}
            aria-pressed={shelf === s.shelf}
            onClick={() => tap(s.shelf)}
            className={`tap inline-flex items-center rounded-pill border px-3 text-sm transition-colors ${
              shelf === s.shelf
                ? "border-accent bg-accent text-on-accent"
                : "border-border text-text-muted hover:bg-surface-2 hover:text-text"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      {error && <p role="alert" data-testid="shelf-error" className="mt-1 text-sm text-danger">{error}</p>}
    </div>
  );
}
