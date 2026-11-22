"use client";

import { Clock, DotsThree, Eye, Heart, PlayCircle } from "@phosphor-icons/react";
import { useEffect, useOptimistic, useRef, useState, useTransition } from "react";

import { reactToTitle, setShelf } from "@/app/(app)/screening/actions";
import type { ActionResult } from "@/lib/action-result";
import { nextShelf, type Shelf } from "@/lib/screening/types";

// A title's buttons as Letterboxd's (owner, 2026-11-22; docs/screening.md §9):
// Watch (Finished), Watching (series only), Like (the love reaction) and
// Watchlist (Want). Watch, Watching and Watchlist are one shelf: lighting one
// moves the title there, tapping the lit one takes it off (db/089). Like is
// apart. Lit buttons fill and say Watched / Liked. Optimistic; the server
// render that follows replaces it. A title not yet in `titles` goes in on
// its first tap (by TMDB id).

type Target = { titleId: number } | { kind: "movie" | "tv"; tmdbId: number };
type Reaction = "love" | "neutral" | "not_interested" | null;

function useSave() {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const run = (optimistic: () => void, action: () => Promise<ActionResult<object>>) =>
    start(async () => {
      optimistic();
      try {
        const r = await action();
        setError(r.ok ? null : r.error);
      } catch {
        setError("That didn't save. Check the connection and try again.");
      }
    });
  return { pending, error, run };
}

export function TitleActions({ target, kind, shelf: current, reaction: currentReaction }: {
  target: Target; kind: "movie" | "tv"; shelf: Shelf | null; reaction: Reaction;
}) {
  const [shelf, setShelfOpt] = useOptimistic(current);
  const [reaction, setReactionOpt] = useOptimistic(currentReaction);
  const { pending, error, run } = useSave();

  const tapShelf = (tapped: Shelf) => {
    const next = nextShelf(shelf, tapped);
    run(() => setShelfOpt(next), () => setShelf({ ...target, shelf: next }));
  };
  const tapLike = () => {
    const next: Reaction = reaction === "love" ? null : "love";
    run(() => setReactionOpt(next), () => reactToTitle({ ...target, reaction: next ?? "none", origin: "title" }));
  };

  const buttons: { id: string; on: boolean; label: [string, string]; icon: React.ReactNode; tone: string; tap: () => void }[] = [
    { id: "watched", on: shelf === "finished", label: ["Watch", "Watched"], tone: "text-hit",
      icon: <Eye size={40} weight={shelf === "finished" ? "fill" : "light"} aria-hidden />, tap: () => tapShelf("finished") },
    ...(kind === "tv" ? [{ id: "watching", on: shelf === "watching", label: ["Watching", "Watching"] as [string, string],
      tone: "text-accent-ink", icon: <PlayCircle size={40} weight={shelf === "watching" ? "fill" : "light"} aria-hidden />,
      tap: () => tapShelf("watching") }] : []),
    { id: "liked", on: reaction === "love", label: ["Like", "Liked"], tone: "text-accent-strong",
      icon: <Heart size={40} weight={reaction === "love" ? "fill" : "light"} aria-hidden />, tap: tapLike },
    { id: "watchlist", on: shelf === "want", label: ["Watchlist", "Watchlist"], tone: "text-accent-ink",
      icon: <Clock size={40} weight={shelf === "want" ? "fill" : "light"} aria-hidden />, tap: () => tapShelf("want") },
  ];

  return (
    <div>
      <div role="group" aria-label="Your record" aria-busy={pending} data-testid="title-actions"
           data-shelf={shelf ?? "none"} data-reaction={reaction ?? "none"}
           className={`grid rounded-inner bg-surface ${kind === "tv" ? "grid-cols-4" : "grid-cols-3"}`}>
        {buttons.map((b) => (
          <button key={b.id} type="button" onClick={b.tap} aria-pressed={b.on} data-testid={`title-${b.id}`}
                  className={`tap flex flex-col items-center gap-1.5 py-4 text-sm transition-colors ${
                    b.on ? b.tone : "text-text-muted hover:text-text"}`}>
            {b.icon}
            <span className={b.on ? "text-text" : ""}>{b.label[b.on ? 1 : 0]}</span>
          </button>
        ))}
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}

/** The "⋯" over the hero: Not interested and Neutral (they teach For you; owner, Nov 22). */
export function TitleMore({ target, reaction: currentReaction }: { target: Target; reaction: Reaction }) {
  const [open, setOpen] = useState(false);
  const [reaction, setReactionOpt] = useOptimistic(currentReaction);
  const { error, run } = useSave();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (r: "not_interested" | "neutral") => {
    const next: Reaction = reaction === r ? null : r;
    setOpen(false);
    run(() => setReactionOpt(next), () => reactToTitle({ ...target, reaction: next ?? "none", origin: "title" }));
  };

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="More" data-testid="title-more"
              className="tap inline-flex size-11 items-center justify-center rounded-pill bg-bg/70 text-text backdrop-blur-sm hover:bg-bg">
        <DotsThree size={26} weight="bold" aria-hidden />
      </button>
      {open && (
        <ul role="menu" data-testid="title-more-menu"
            className="absolute right-0 top-full z-30 mt-2 w-56 overflow-hidden rounded-inner border border-border bg-surface">
          {([["not_interested", "Not interested"], ["neutral", "Neutral"]] as const).map(([r, label]) => (
            <li key={r}>
              <button type="button" role="menuitemcheckbox" aria-checked={reaction === r} onClick={() => choose(r)}
                      data-testid={`title-${r.replace("_", "-")}`}
                      className="flex w-full items-center justify-between border-b border-border px-4 py-3 text-left text-base text-text hover:bg-band">
                {label}
                {reaction === r && <span className="label-mono text-accent-ink">On</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" className="absolute right-0 top-full mt-2 w-56 text-sm text-danger">{error}</p>}
    </div>
  );
}
