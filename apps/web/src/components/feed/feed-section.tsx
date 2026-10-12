import Link from "next/link";

import type { FeedStory, FeedTab } from "@/lib/feed/types";

import { FeedList } from "./feed-list";

// Home's feed: two tabs over the home pillar.
//   For you  the learned order (db/039 feed_for_you): the interest profile's
//            score minus recency, Alabama near the top, at most 5 tier-1
//            sports stories on page one. The default.
//   Latest   newest first (Phase 1's order).
// Both leave out what the owner dismissed. 30 a page; older pages are
// ?foryou=N or ?feed=latest&latest=N on Home itself, anchored to #stories.

const TABS: { tab: FeedTab; label: string; href: string }[] = [
  { tab: "for-you", label: "For you", href: "/#stories" },
  { tab: "latest", label: "Latest", href: "/?feed=latest#stories" },
];

function pageHref(tab: FeedTab, p: number): string {
  if (tab === "latest") return p <= 1 ? "/?feed=latest#stories" : `/?feed=latest&latest=${p}#stories`;
  return p <= 1 ? "/#stories" : `/?foryou=${p}#stories`;
}

export function FeedSection({
  tab,
  stories,
  page,
  hasMore,
  now,
}: {
  tab: FeedTab;
  stories: FeedStory[];
  page: number;
  hasMore: boolean;
  now: number;
}) {
  const label = tab === "latest" ? "Latest" : "For you";
  return (
    <section id="stories" aria-label="Feed" data-testid="feed" className="mt-14 scroll-mt-6 lg:max-w-3xl">
      <h2 className="sr-only">
        {label}
        {page > 1 ? ` · page ${page}` : ""}
      </h2>
      {/* No negative margin: the active pill starts on the cards' left edge. */}
      <nav aria-label="Feed order" data-testid="feed-tabs" className="flex items-center gap-1">
        {TABS.map((t) => (
          <Link
            key={t.tab}
            href={t.href}
            data-testid={`feed-tab-${t.tab}`}
            aria-current={t.tab === tab ? "page" : undefined}
            className={`tap inline-flex items-center rounded-pill px-3 label-mono transition-colors ${
              t.tab === tab ? "bg-surface-2 text-text" : "text-text-muted hover:text-text"
            }`}
          >
            {t.label}
          </Link>
        ))}
        {page > 1 && <span className="label-mono ml-2 text-text-muted">Page {page}</span>}
      </nav>

      {stories.length === 0 ? (
        <p data-testid="feed-empty" className="mt-3 border-t border-border pt-4 text-base text-text-muted">
          {page > 1
            ? "Nothing older."
            : tab === "for-you"
              ? "Nothing ranked yet. For you fills in after the worker's next profile rebuild; Latest has every story now."
              : "No stories yet. They arrive with the worker's next feed run."}
        </p>
      ) : (
        <FeedList key={`${tab}-${page}`} stories={stories} pillar="home" now={now} testId={tab === "latest" ? "feed-latest" : "feed-for-you"} />
      )}

      {(page > 1 || hasMore) && (
        <nav aria-label={`${label} pages`} className="mt-4 -ml-3 flex items-center gap-1">
          {page > 1 && (
            <Link href={pageHref(tab, page - 1)} className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2">
              {tab === "latest" ? "Newer" : "Back"}
            </Link>
          )}
          {hasMore && (
            <Link
              data-testid="feed-older"
              href={pageHref(tab, page + 1)}
              className="tap inline-flex items-center rounded-pill px-3 text-sm text-text transition-colors hover:bg-surface-2"
            >
              {tab === "latest" ? "Older stories" : "More"}
            </Link>
          )}
        </nav>
      )}
    </section>
  );
}
