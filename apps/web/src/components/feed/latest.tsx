import Link from "next/link";

import { relativeTime, type FeedStory } from "@/lib/feed/types";

import { FeedCard } from "./feed-card";

// Home's "Latest": home-pillar stories, newest first, 30 a page. Older pages
// are ?latest=N on Home itself, anchored back to this section.

export function Latest({ stories, page, hasMore, now }: { stories: FeedStory[]; page: number; hasMore: boolean; now: number }) {
  const href = (p: number) => (p <= 1 ? "/#latest" : `/?latest=${p}#latest`);
  return (
    <section id="latest" aria-label="Latest" data-testid="feed-latest" className="mt-14 scroll-mt-6 lg:max-w-3xl">
      <h2 className="label-mono text-text-muted">
        Latest{page > 1 ? ` · page ${page}` : ""}
      </h2>
      {stories.length === 0 ? (
        <p data-testid="feed-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
          {page > 1 ? "Nothing older." : "No stories yet. They arrive with the worker's next feed run."}
        </p>
      ) : (
        <ul className="mt-2 border-t border-border">
          {stories.map((s) => (
            <FeedCard key={s.id} story={s} when={relativeTime(s.publishedAt, now)} />
          ))}
        </ul>
      )}
      {(page > 1 || hasMore) && (
        <nav aria-label="Latest pages" className="mt-4 -ml-3 flex items-center gap-1">
          {page > 1 && (
            <Link href={href(page - 1)} className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2">
              Newer
            </Link>
          )}
          {hasMore && (
            <Link
              data-testid="feed-older"
              href={href(page + 1)}
              className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2"
            >
              Older stories
            </Link>
          )}
        </nav>
      )}
    </section>
  );
}
