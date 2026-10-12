"use client";

import { ArrowUpRight, CaretDown } from "@phosphor-icons/react";
import { useId, useState } from "react";

import type { FeedStory } from "@/lib/feed/types";

// One story in a feed list: a hairline row, not a boxed card (ledger
// language, as the brief). Mono "SOURCE · 3H", the headline in Instrument
// Serif, Haiku's summary (or nothing: the headline stands alone), and a
// "+N outlets" fold when other outlets carry the story. The headline's link
// covers the whole row, so a tap anywhere opens the publisher in a new tab;
// the fold sits above that layer.

export function FeedCard({ story, when }: { story: FeedStory; when: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const n = story.outlets.length;
  return (
    <li data-testid="feed-card" data-story={story.id} className="relative border-b border-border py-5">
      <p className="label-mono flex flex-wrap items-center gap-x-2 gap-y-1 text-text-muted">
        <span data-testid="feed-meta">
          {story.sourceName} · {when}
        </span>
        {story.isTest && (
          <span data-testid="feed-test" className="rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">
            test
          </span>
        )}
      </p>
      <h3 className="mt-2 font-display text-xl text-text text-balance lg:text-2xl">
        <a
          data-testid="feed-link"
          href={story.url}
          target="_blank"
          rel="noopener noreferrer"
          className="transition-colors after:absolute after:inset-0 hover:text-accent focus-visible:outline-none focus-visible:after:rounded-inner focus-visible:after:ring-2 focus-visible:after:ring-accent"
        >
          {story.title}
        </a>
      </h3>
      {story.summary && (
        <p data-testid="feed-summary" className="mt-2 text-base text-text-muted text-pretty">
          {story.summary}
        </p>
      )}
      {n > 0 && (
        <div className="relative z-10 mt-2 -ml-3">
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
          {open && (
            <ul id={id} data-testid="feed-outlets" className="ml-3 mt-1 flex flex-col">
              {story.outlets.map((o) => (
                <li key={o.name}>
                  <a
                    href={o.url}
                    target="_blank"
                    rel="noopener noreferrer"
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
      )}
    </li>
  );
}
