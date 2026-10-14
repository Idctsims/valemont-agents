"use client";

import { ArrowUpRight, CaretDown, EyeSlash, Heart, Minus, Star } from "@phosphor-icons/react";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import {
  REPO_TOPICS_SHOWN, SWIPE_COMMIT_PX, starCount, stars7dText, swipeReaction, type FeedStory, type Pillar, type RepoInfo,
  type TrailerMatch,
} from "@/lib/feed/types";

// One story in a feed list: a hairline row, not a boxed card (ledger
// language, as the brief). Mono "SOURCE · 3H" (plus OFFICIAL for a
// first-party tech source), the headline in Instrument
// Serif, Haiku's summary (or nothing: the headline stands alone), the "Why it
// matters" line when the story has one, a "+N outlets" fold when other outlets
// carry the story, and the three reactions. A GitHub repo story (db/049) adds
// its stars, stars over 7 days (labelled when it is an estimate), language
// and up to 3 topics; its headline is owner/name and links to the repo. The headline's link covers the
// whole row, so a tap anywhere opens the publisher in a new tab; the fold and
// the reaction buttons sit above that layer and never open it.
//
// Gestures, each also a button (owner, Phase 2):
//   swipe right  Love
//   swipe left   Not interested
// A swipe never opens the publisher: the click that ends a drag is dropped.
// Opening the publisher sends a beacon (/api/feed/open): a weak Love signal.

export type CardReact = (reaction: "love" | "neutral" | "not_interested") => void;

/** Movement before a drag picks an axis; below this it is still a tap. */
const AXIS_PX = 8;
/** The swipe's icon (22 px) shows once the uncovered strip holds it with 5 px each side. */
const REVEAL_ICON_PX = 32;

function sendOpen(storyId: number, pillar: Pillar) {
  try {
    navigator.sendBeacon?.("/api/feed/open", new Blob([JSON.stringify({ storyId, pillar })], { type: "application/json" }));
  } catch {
    // A lost beacon is a lost weak signal, never an error for the owner.
  }
}

export function FeedCard({
  story,
  when,
  pillar = "home",
  onReact,
}: {
  story: FeedStory;
  when: string;
  pillar?: Pillar;
  /** Absent: a static card (no reactions, no swipe, no beacon). */
  onReact?: CardReact;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const n = story.outlets.length;
  const live = !!onReact;

  // ---- swipe
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; axis: "x" | "y" | null } | null>(null);
  const swiped = useRef(false);

  function onPointerDown(e: React.PointerEvent) {
    if (!live || e.button !== 0) return;
    // The link overlay starts a swipe; the buttons and outlet links do not.
    if ((e.target as Element).closest("button, [data-no-swipe]")) return;
    drag.current = { x: e.clientX, y: e.clientY, axis: null };
    swiped.current = false;
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const mx = e.clientX - d.x;
    const my = e.clientY - d.y;
    if (!d.axis) {
      if (Math.abs(mx) < AXIS_PX && Math.abs(my) < AXIS_PX) return;
      d.axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
      if (d.axis === "x") {
        (e.currentTarget as Element).setPointerCapture(e.pointerId);
        swiped.current = true;
        setDragging(true);
      }
    }
    if (d.axis !== "x") return;
    setDx(Math.max(-SWIPE_COMMIT_PX * 1.4, Math.min(SWIPE_COMMIT_PX * 1.4, mx)));
  }

  function onPointerEnd() {
    const d = drag.current;
    drag.current = null;
    if (!d || d.axis !== "x") return;
    setDragging(false);
    const r = swipeReaction(dx);
    setDx(0);
    if (r) onReact?.(r);
  }

  /** The click that ends a swipe would open the publisher: drop it. */
  function onClickCapture(e: React.MouseEvent) {
    if (swiped.current) {
      e.preventDefault();
      e.stopPropagation();
      swiped.current = false;
    }
  }

  return (
    <li
      data-testid="feed-card"
      data-story={story.id}
      data-loved={story.loved || undefined}
      className="relative overflow-hidden border-b border-border"
    >
      {/* Behind the row: Love (right swipe) and Not interested (left), the
          icon alone, centred in exactly the strip the swipe has uncovered. It
          shows only once the strip holds it whole, and dims until a release
          would commit. A text label clipped ("LO", "ED") at short distances. */}
      {live && dx !== 0 && (
        <div
          aria-hidden
          data-testid="feed-swipe-reveal"
          style={{ width: Math.abs(dx) }}
          className={`absolute inset-y-0 flex items-center justify-center overflow-hidden ${dx > 0 ? "left-0 text-accent" : "right-0 text-text-muted"}`}
        >
          {Math.abs(dx) >= REVEAL_ICON_PX && (
            <span className={`transition-opacity ${swipeReaction(dx) ? "opacity-100" : "opacity-60"}`}>
              {dx > 0 ? <Heart size={22} weight="fill" /> : <EyeSlash size={22} />}
            </span>
          )}
        </div>
      )}

      <div
        data-testid="feed-card-body"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onClickCapture={onClickCapture}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
        className={`relative touch-pan-y bg-bg pt-5 ${live ? "pb-1" : "pb-5"} ${dragging ? "" : "transition-transform"}`}
      >
        <p className="label-mono flex flex-wrap items-center gap-x-2 gap-y-1 text-text-muted">
          <span data-testid="feed-meta">
            {story.sourceName} · {when}
          </span>
          {/* A first-party source (db/045): shown with or without a summary,
              so a fresh release reads as official before Haiku has run. */}
          {story.official && (
            <span data-testid="feed-official" className="rounded-pill border border-accent px-2 text-accent">
              Official
            </span>
          )}
          {story.isTest && (
            <span data-testid="feed-test" className="rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">
              test
            </span>
          )}
        </p>
        {/* text-lg at 390 px (17 px): 19 of 30 real page-one headlines fit in
            two lines and none runs past three; text-xl fit 15, with 5 at four
            lines (measured 2026-10-12, built CSS and Instrument Serif). */}
        <h3 className="mt-2 font-display text-lg text-text text-balance lg:text-xl">
          <a
            data-testid="feed-link"
            href={story.url}
            target="_blank"
            rel="noopener noreferrer"
            draggable={false}
            onClick={live ? () => sendOpen(story.id, pillar) : undefined}
            className="transition-colors after:absolute after:inset-0 hover:text-accent focus-visible:outline-none focus-visible:after:rounded-inner focus-visible:after:ring-2 focus-visible:after:ring-accent"
          >
            {story.title}
          </a>
        </h3>
        {story.summary && (
          <p data-testid="feed-summary" className="mt-2 text-sm text-text-muted text-pretty">
            {story.summary}
          </p>
        )}
        {story.repo && <RepoFacts repo={story.repo} />}
        {story.trailer && <TrailerTitle trailer={story.trailer} />}
        {story.whyItMatters && (
          <p data-testid="feed-why" className="mt-3 border-l-2 border-accent pl-3 text-sm text-text text-pretty">
            <span className="label-mono mr-2 text-accent">Why it matters</span>
            {story.whyItMatters}
          </p>
        )}

        <div className="relative z-10 mt-1 -ml-3 flex flex-wrap items-center justify-between gap-x-2">
          {n > 0 ? (
            <button
              type="button"
              data-testid="feed-outlets-toggle"
              aria-expanded={open}
              aria-controls={id}
              onClick={() => setOpen((o) => !o)}
              className="tap inline-flex items-center gap-2 rounded-pill px-3 font-mono text-xs text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
            >
              +{n} {n === 1 ? "outlet" : "outlets"}
              <CaretDown size={12} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} />
            </button>
          ) : (
            <span />
          )}
          {live && <Reactions story={story} onReact={onReact!} />}
        </div>
        {n > 0 && open && (
          <ul id={id} data-testid="feed-outlets" className="relative z-10 mb-3 flex flex-col">
            {story.outlets.map((o) => (
              <li key={o.name}>
                <a
                  href={o.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-no-swipe
                  onClick={live ? () => sendOpen(story.id, pillar) : undefined}
                  className="tap inline-flex items-center gap-1.5 text-sm text-text transition-colors hover:text-accent"
                >
                  {o.name}
                  <ArrowUpRight size={14} aria-hidden />
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

/** A GitHub repo story's facts (db/049): stars, stars over 7 days with
 * where the number came from (only "7d" is measured; every estimate says
 * so), the language and at most 3 topics. */
function RepoFacts({ repo }: { repo: RepoInfo }) {
  const delta = stars7dText(repo);
  const estimate = repo.stars7dSource !== null && repo.stars7dSource !== "measured";
  return (
    <div data-testid="repo-facts" className="mt-3 flex flex-col gap-2">
      <p className="label-mono flex flex-wrap items-center gap-x-3 gap-y-1 normal-case tracking-normal text-text-muted">
        {repo.stars !== null && (
          <span data-testid="repo-stars" className="inline-flex items-center gap-1 text-text">
            <Star size={14} weight="fill" aria-hidden className="text-accent" />
            {starCount(repo.stars)}
            <span className="sr-only"> stars</span>
          </span>
        )}
        {delta && (
          <span data-testid="repo-stars-7d" data-estimate={estimate || undefined}>
            {delta}
          </span>
        )}
        {repo.language && <span data-testid="repo-language">{repo.language}</span>}
      </p>
      {repo.topics.length > 0 && (
        <ul aria-label="Topics" className="flex flex-wrap gap-1.5">
          {repo.topics.slice(0, REPO_TOPICS_SHOWN).map((t) => (
            <li key={t} data-testid="repo-topic" className="rounded-pill border border-border px-2 font-mono text-xs text-text-muted">
              {t}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A trailer's TMDB title (db/051): a link to its title page, above the row's
 * own link, or a plain "Not matched to a title" when the worker found none. */
function TrailerTitle({ trailer }: { trailer: TrailerMatch }) {
  if (trailer.status === "unmatched") {
    return (
      <p data-testid="trailer-unmatched" className="label-mono mt-2 text-text-muted">
        <span className="rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">unmatched</span>
        <span className="ml-2 normal-case tracking-normal">Not matched to a title</span>
      </p>
    );
  }
  return (
    <p className="relative z-10 mt-2">
      <Link
        href={`/screening/title/${trailer.kind}/${trailer.tmdbId}`}
        data-testid="trailer-title"
        data-no-swipe
        className="tap -ml-1 inline-flex items-center gap-1.5 px-1 text-sm text-accent transition-colors hover:text-text"
      >
        {trailer.name}
        <span className="label-mono text-text-muted">{trailer.kind === "tv" ? "Series" : "Film"}</span>
        <ArrowUpRight size={14} aria-hidden />
      </Link>
    </p>
  );
}

function Reactions({ story, onReact }: { story: FeedStory; onReact: CardReact }) {
  const btn = "tap inline-flex items-center justify-center rounded-pill transition-colors hover:bg-surface-2";
  return (
    <div role="group" aria-label="React" data-testid="feed-reactions" className="-mr-3 flex items-center">
      <button
        type="button"
        data-testid="react-love"
        aria-pressed={story.loved}
        aria-label={story.loved ? "Loved. Tap to take it back" : "Love"}
        title="Love"
        onClick={() => onReact("love")}
        className={`${btn} ${story.loved ? "text-accent" : "text-text-muted hover:text-text"}`}
      >
        <Heart size={20} weight={story.loved ? "fill" : "regular"} aria-hidden />
      </button>
      <button
        type="button"
        data-testid="react-neutral"
        aria-label="Neutral"
        title="Neutral"
        onClick={() => onReact("neutral")}
        className={`${btn} text-text-muted hover:text-text`}
      >
        <Minus size={20} aria-hidden />
      </button>
      <button
        type="button"
        data-testid="react-not-interested"
        aria-label="Not interested"
        title="Not interested"
        onClick={() => onReact("not_interested")}
        className={`${btn} text-text-muted hover:text-text`}
      >
        <EyeSlash size={20} aria-hidden />
      </button>
    </div>
  );
}
