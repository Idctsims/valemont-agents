"use client";

import { useOptimistic, useState, useTransition } from "react";

import { EyeSlash, Heart, Minus } from "@phosphor-icons/react";

import { reactToTitle } from "@/app/(app)/screening/actions";
import type { ActionResult } from "@/lib/action-result";

// Love / Neutral / Not interested on a title page (Chat 4 Phase 4; db/053).
// The current reaction is pressed; tapping it again takes it back. A title
// not yet in the database goes in first (by its TMDB id). Optimistic; the
// server render that follows replaces it.

type Reaction = "love" | "neutral" | "not_interested";
type Target = { titleId: number } | { kind: "movie" | "tv"; tmdbId: number };

const BUTTONS: { r: Reaction; label: string; testId: string }[] = [
  { r: "love", label: "Love", testId: "title-love" },
  { r: "neutral", label: "Neutral", testId: "title-neutral" },
  { r: "not_interested", label: "Not interested", testId: "title-not-interested" },
];

export function TitleReactions({ target, current }: { target: Target; current: Reaction | null }) {
  const [reaction, setReaction] = useOptimistic(current);
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function tap(r: Reaction) {
    const next = r === reaction ? null : r;
    start(async () => {
      setReaction(next);
      try {
        const res: ActionResult = await reactToTitle({ ...target, reaction: next ?? "none", origin: "title" });
        setError(res.ok ? null : res.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  }

  return (
    <div className="shrink-0">
      <div role="group" aria-label="Your take" aria-busy={pending} data-testid="title-reactions" data-reaction={reaction ?? "none"}
           className="flex flex-wrap items-center gap-1">
        {BUTTONS.map(({ r, label, testId }) => {
          const on = reaction === r;
          const Icon = r === "love" ? Heart : r === "neutral" ? Minus : EyeSlash;
          return (
            <button key={r} type="button" data-testid={testId} aria-pressed={on} onClick={() => tap(r)}
                    className={`tap inline-flex items-center gap-1.5 rounded-pill border px-3 text-sm transition-colors ${
                      on ? "border-accent bg-accent text-on-accent" : "border-border text-text-muted hover:bg-surface-2 hover:text-text"
                    }`}>
              <Icon size={16} weight={on && r === "love" ? "fill" : "regular"} aria-hidden />
              {label}
            </button>
          );
        })}
      </div>
      {error && <p role="alert" data-testid="title-reaction-error" className="mt-1 text-sm text-danger">{error}</p>}
    </div>
  );
}
